# Hotieler design notes

This document is an interview aid for the backend implementation. The README remains the
entry point for running and exercising the service.

## Dependency direction

```mermaid
flowchart TD
    API[FastAPI routes and Pydantic schemas] --> APP[Application services]
    APP --> DOMAIN[Domain entities, value objects, policies, specifications]
    APP --> PORTS[Repository, payment, clock, ID and lock protocols]
    INFRA[In-memory repositories, mock payments, system clock, keyed locks] --> PORTS
    ROOT[Explicit composition root] --> API
    ROOT --> APP
    ROOT --> INFRA
```

The domain has no dependency on FastAPI, Pydantic, or infrastructure. Routes validate and
map transport data; application services coordinate use cases; entities and policies own
business invariants; adapters provide replaceable side effects.

## Domain relationships

```mermaid
erDiagram
    OWNER_ACCOUNT ||--o{ PROPERTY : owns
    PROPERTY ||--|{ ROOM_TYPE : offers
    PROPERTY ||--o{ BOOKING : receives
    ROOM_TYPE ||--o{ BOOKING : reserves
    BOOKING ||--o{ PAYMENT_RECORD : has

    OWNER_ACCOUNT {
        UUID id
        string name
        string contact_email
    }
    PROPERTY {
        UUID id
        UUID owner_id
        string city
        string locality
        Decimal star_rating
    }
    ROOM_TYPE {
        UUID id
        UUID property_id
        int total_units
        int guests_per_unit
        Money nightly_rate
    }
    BOOKING {
        UUID id
        StayPeriod stay
        int guest_count
        int required_units
        Money total_price
        BookingStatus status
    }
    PAYMENT_RECORD {
        UUID id
        PaymentMethod method
        PaymentStatus status
        string idempotency_key
        string fingerprint
    }
```

`StayPeriod` is a half-open interval: `[check_in, check_out)`. `Money` and `StayPeriod`
are immutable value objects. A booking snapshots the required units and price so later
catalog changes cannot rewrite an accepted quote.

## Booking state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING_PAYMENT: inventory held
    PENDING_PAYMENT --> CONFIRMED: payment approved
    PENDING_PAYMENT --> PAYMENT_FAILED: payment rejected / release inventory
    PENDING_PAYMENT --> CANCELLED: cancel / release inventory / no refund
    CONFIRMED --> CANCELLED: cancel / policy refund / release inventory
    PAYMENT_FAILED --> [*]
    CANCELLED --> [*]
```

Only pending and confirmed bookings reserve inventory. Transition methods live on
`Booking`; invalid transitions are domain conflicts. Cancellation records its result so a
safe replay cannot release inventory or refund twice.

## Availability and booking race

```mermaid
sequenceDiagram
    participant A as Request A
    participant B as Request B
    participant L as Room-type lock
    participant R as Booking repository

    A->>L: acquire room_type:{id}
    B->>L: wait
    A->>R: sum overlapping active units
    A->>R: save PENDING_PAYMENT booking
    A-->>L: release
    L-->>B: acquire
    B->>R: sum overlapping active units again
    R-->>B: no remaining inventory
    B-->>B: raise ROOM_INVENTORY_UNAVAILABLE
    B-->>L: release
```

Search never promises inventory: it is an advisory read. Booking creation performs the
same availability calculation inside the keyed critical section. Lock granularity is a
room type, so unrelated inventory can be booked concurrently. The lock is process-local,
matching the in-memory/single-worker scope; a multi-instance design would move the
invariant into a transactional database or distributed inventory allocator.

## Extension examples

### Payment method

Implement the `PaymentProcessor` protocol for the new method and register it by method
key. Booking/payment services continue to depend on the common contract; their state
transition logic is unchanged.

### Search filter

Add a specification/predicate for the new criterion and compose it into the search query.
Inventory evaluation and result mapping remain unchanged. This avoids a growing conditional
chain in the API route.

### Cancellation policy

Implement the `CancellationPolicy` protocol and inject the selected policy into the
cancellation service. The service still owns orchestration while the policy owns refund
calculation and cutoff rules.

### Pricing

Implement `PricingStrategy` and inject it into booking/search services. The standard
strategy remains `nights * required_units * nightly_rate`; no speculative dynamic-pricing
rules are included.

## Trade-offs

| Decision | Benefit | Cost / boundary |
|---|---|---|
| In-memory repositories behind protocols | Small, deterministic assessment slice | Restart loses state; no cross-process transactions |
| One Uvicorn worker | Process-local locks enforce the invariant | Vertical-only runtime until storage/locking changes |
| Pending booking as inventory hold | Payment cannot oversell after booking creation | Holds never expire in this version |
| Deterministic mock payment | Success and failure are testable without credentials | No gateway webhook or uncertain external outcome |
| Booking-time snapshots | Historical price and capacity calculation stay stable | Catalog edits do not reprice existing bookings |
| Calendar-day refund policy | Boundaries are simple and testable | Real systems need time zone, property, and rate-plan rules |
| Request fingerprint idempotency | Safe payment replay with conflict detection | Durable storage is required before horizontal scaling |

## Likely follow-up questions

**Why not a generic repository or base service?** Narrow use-case protocols expose only
operations consumers need. Generic abstractions would hide domain intent without buying a
real substitution point.

**How would PostgreSQL change booking?** In one transaction, lock the room-type inventory
row (or use an atomic capacity ledger), recompute overlapping active reservations, insert
the booking, and commit. Add database uniqueness for idempotency keys and use retry rules
for serialization/deadlock errors.

**How would payment become asynchronous?** Persist a payment attempt before dispatch,
model pending/unknown gateway outcomes, consume signed webhooks idempotently, and reconcile
ambiguous attempts rather than retrying blindly.

**How would holds expire?** Give pending bookings an expiry timestamp, exclude expired
holds during authoritative availability checks, and run an idempotent cleanup/reconciliation
job. Payment confirmation must atomically reject an already expired hold.

**Why store required units and quoted total?** They are facts accepted when the booking was
made. Recomputing them from mutable room capacity or rates would corrupt history and refund
calculations.
