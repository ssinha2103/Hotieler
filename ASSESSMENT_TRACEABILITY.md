# Assessment traceability

This document is the repository audit index for the hotel-booking machine-coding
submission. It maps each requirement, review criterion, deliverable, bonus, and scope
guardrail from the official brief to the public HTTP contract, implementation symbols,
focused tests, and a reproducible API walkthrough.

The confidential assessment document is intentionally **not** committed, linked, or
reproduced here. Requirement descriptions below are concise paraphrases used only for
traceability.

> **Approved language substitution:** the official brief names Java 17 and Spring Boot.
> The recruiter approved Python 3.12 and FastAPI for this submission. The approval
> correspondence remains external to this public repository and should be retained with
> the submission evidence.

## Status legend

| Status | Meaning |
|---|---|
| **COMPLETE** | Implemented, reachable where applicable, and covered by a named test or an explicit repository artifact. |
| **PARTIAL** | The repository contains meaningful evidence, but completion also depends on external approval, human readiness, elapsed-time provenance, or final verification. |
| **OUT-OF-SCOPE** | Deliberately excluded by the brief or by an explicit project boundary. |

Test names are written exactly as collected by pytest. Links are repository-relative so
they continue to work after cloning.

## Coverage summary

| Audit surface | Accounted for | Current assessment |
|---|---:|---|
| Mandatory functional requirements | 15 of 15 | **COMPLETE** |
| Evaluation-rubric areas | 6 of 6 | **COMPLETE** |
| Optional bonus items | 4 of 4 | **COMPLETE** |
| Required deliverable categories | 3 of 3 | Implemented; the mandated Java/Spring stack remains a **PARTIAL** recruiter-approved exception. |
| Explicit scope exclusions | 4 of 4 | **OUT-OF-SCOPE** by design, with the boundary recorded below. |
| Automated test suite | 122 unit, integration, and concurrency tests | **PASS** in the current Docker `make verify` run. |
| Ruff formatting/lint, mypy, runtime smoke | 4 gates | **PASS** in the current Docker-backed verification runs. |
| Branch-aware coverage | 94.85% (threshold: 90%) | **PASS** in the current Docker `make verify` run. |
| Staged publication audit | Tracked/staged paths, whitespace, secret signatures, and confidential-file names | **PASS** for the current staged change. |
| Remote CI | GitHub Actions Docker verification | [Run 36242516289](https://github.com/ssinha2103/Hotieler/actions/runs/36242516289) is a prior published baseline for commit `fa4193d`; a new run is required for later changes. |

## 1. Round overview and delivery contract

| Brief point | Status | Repository evidence |
|---|---|---|
| Build a working backend that models a non-trivial hotel-booking domain. | **COMPLETE** | The application factory [`create_app`](src/hotieler/main.py), composition root [`build_container`](src/hotieler/container.py), and versioned routes in [`api/routes.py`](src/hotieler/api/routes.py) expose the complete flow. `test_approved_payment_replay_cancellation_and_inventory_release` in [`test_api_flows.py`](tests/integration/test_api_flows.py) exercises it through HTTP. |
| Prefer a small, clean, extensible slice over feature breadth. | **COMPLETE** | The dependency direction is API -> application -> domain/ports, with adapters composed at the root. [`DESIGN.md`](DESIGN.md) records the boundaries and rejected complexity. |
| Complete the exercise offline in the candidate's environment. | **COMPLETE** | [`Dockerfile`](Dockerfile), [`compose.yaml`](compose.yaml), and [`run.sh`](run.sh) make the evaluator environment reproducible without host Python. |
| Complete within the stated take-home time window. | **PARTIAL** | Elapsed-time provenance cannot be established by source code and must be represented honestly by the candidate's submission history. |
| Use Java 17 and Spring Boot. | **PARTIAL** - recruiter-approved exception | Python 3.12 and FastAPI are used under the recruiter exception stated above. The requested behavior is implemented, but the repository does not satisfy the brief's literal technology choice. Written approval is intentionally kept outside this public repository. |
| Deliver a runnable service plus a concise README through a repository or archive. | **COMPLETE** | [`README.md`](README.md) contains startup, Swagger, assumptions, verification commands, observability, and production-evolution notes, and links to [`DESIGN.md`](DESIGN.md) for the detailed decisions. The public repository is the delivery vehicle. |

## 2. Functional requirements

### 2.1 Property discovery

| Brief point | Status | Endpoint and evidence | Exact implementation symbols | Exact tests |
|---|---|---|---|---|
| Search by city/locality, stay dates, and guest count. | **COMPLETE** | `GET /api/v1/properties/search`; create catalog data through the onboarding endpoints or opt in locally with `./run.sh seed`. | [`search_properties`](src/hotieler/api/routes.py), [`AvailabilitySearchService.search`](src/hotieler/application/services.py), [`SearchQuery`](src/hotieler/application/models.py), [`CitySpecification`](src/hotieler/domain/specifications.py), [`LocalitySpecification`](src/hotieler/domain/specifications.py). | `test_search_applies_all_filters_and_deterministic_order`, `test_chain_search_multi_unit_hold_adjacency_and_pending_cancellation_flow`. |
| Support price, amenity, and star-rating filters without making future filters invasive. | **COMPLETE** | The same search endpoint accepts `min_price`, `max_price`, repeatable `amenities`, and `min_star_rating`. | [`AvailabilitySearchService._build_specification`](src/hotieler/application/services.py), [`NightlyRateSpecification`](src/hotieler/domain/specifications.py), [`AmenitiesSpecification`](src/hotieler/domain/specifications.py), [`MinStarRatingSpecification`](src/hotieler/domain/specifications.py), [`AndSpecification`](src/hotieler/domain/specifications.py). | `test_search_applies_all_filters_and_deterministic_order`, `test_search_rejects_invalid_business_queries`, `test_equal_search_quotes_have_a_stable_room_identifier_tiebreaker`. |
| Return only inventory available for the requested dates. | **COMPLETE** | Search after creating an overlapping hold; the exhausted room option disappears. Search after rejection/cancellation; it returns. | [`AvailabilitySearchService.search`](src/hotieler/application/services.py), [`_reserved_units`](src/hotieler/application/services.py), [`Booking.reserves_inventory`](src/hotieler/domain/entities.py), [`StayPeriod.overlaps`](src/hotieler/domain/value_objects.py). | `test_search_excludes_only_overlapping_active_inventory`, `test_search_reports_partial_remaining_inventory`, `test_rejected_payment_returns_200_and_immediately_releases_inventory`. |

### 2.2 Property onboarding and ownership

| Brief point | Status | Endpoint and evidence | Exact implementation symbols | Exact tests |
|---|---|---|---|---|
| Onboard property identity, location, room types, amenities, capacity, and pricing. | **COMPLETE** | `POST /api/v1/owners/{owner_id}/properties` with nested room types. | [`create_property`](src/hotieler/api/routes.py), [`CreatePropertyRequest`](src/hotieler/api/schemas.py), [`CatalogService.create_property`](src/hotieler/application/services.py), [`Property`](src/hotieler/domain/entities.py), [`RoomType`](src/hotieler/domain/entities.py). | `test_property_onboarding_requires_an_existing_owner`, `test_onboarding_and_booking_resource_errors_preserve_context`. |
| Model both one-property owners and chains under one owner account. | **COMPLETE** | Create one owner with `POST /api/v1/owners`, then invoke property creation once or multiple times for that owner. | [`OwnerAccount`](src/hotieler/domain/entities.py), [`Property.owner_id`](src/hotieler/domain/entities.py), [`CatalogService.create_owner`](src/hotieler/application/services.py), [`CatalogService.create_property`](src/hotieler/application/services.py). | `test_catalog_models_standalone_and_chain_ownership`, `test_chain_search_multi_unit_hold_adjacency_and_pending_cancellation_flow`. |
| Treat a standalone property as the natural one-item case, not a special subtype/path. | **COMPLETE** | The same owner and property endpoints cover both cases. | There is one [`OwnerAccount`](src/hotieler/domain/entities.py) and one [`Property`](src/hotieler/domain/entities.py) model; no standalone/chain subclasses or conditional route exist. | `test_catalog_models_standalone_and_chain_ownership`. |

### 2.3 Booking

| Brief point | Status | Endpoint and evidence | Exact implementation symbols | Exact tests |
|---|---|---|---|---|
| Book a specific property and room type for dates and a guest count. | **COMPLETE** | `POST /api/v1/bookings`; use IDs returned by the ordinary owner/property onboarding flow or by the optional API-driven `./run.sh seed` command. | [`create_booking`](src/hotieler/api/routes.py), [`BookingService.create`](src/hotieler/application/services.py), [`CreateBookingCommand`](src/hotieler/application/models.py), [`RoomType.units_for`](src/hotieler/domain/entities.py). | `test_booking_holds_inventory_and_snapshots_quote`, `test_booking_validates_property_room_relationship`. |
| Recheck availability authoritatively and prevent overlapping double booking. | **COMPLETE** | Submit simultaneous booking requests against one remaining unit; one succeeds and the other receives an inventory conflict. | [`BookingService.create`](src/hotieler/application/services.py), [`InMemoryKeyedLockManager.lock`](src/hotieler/infrastructure/locking.py), [`_reserved_units`](src/hotieler/application/services.py), [`StayPeriod.overlaps`](src/hotieler/domain/value_objects.py). | `test_simultaneous_booking_attempts_have_exactly_one_winner`, `test_concurrent_two_unit_requests_never_exceed_three_unit_inventory`, `test_booking_service_allows_adjacent_and_disjoint_stays_on_one_unit`. |
| Produce a booking with explicit lifecycle states. | **COMPLETE** | Create, pay or reject, fetch, and cancel through the booking/payment endpoints. | [`BookingStatus`](src/hotieler/domain/enums.py), [`Booking.confirm`](src/hotieler/domain/entities.py), [`Booking.mark_payment_failed`](src/hotieler/domain/entities.py), [`Booking.cancel`](src/hotieler/domain/entities.py). | `test_booking_owns_confirmation_and_failed_payment_transitions`, `test_booking_rejects_illegal_transitions`, `test_processed_booking_states_require_payment_identifier`. |

### 2.4 Payment

| Brief point | Status | Endpoint and evidence | Exact implementation symbols | Exact tests |
|---|---|---|---|---|
| Take payment for a pending booking. | **COMPLETE** | `POST /api/v1/bookings/{booking_id}/payments` with `Idempotency-Key`. | [`process_payment`](src/hotieler/api/routes.py), [`PaymentService.process`](src/hotieler/application/services.py), [`PaymentRecord`](src/hotieler/domain/entities.py). | `test_approved_payment_replay_cancellation_and_inventory_release`, `test_invalid_state_is_checked_before_payment_processor`. |
| Support card, UPI, and wallet through a common extension seam. | **COMPLETE** | Select `CARD`, `UPI`, or `WALLET` in Swagger; no credentials are accepted. | [`PaymentMethod`](src/hotieler/domain/enums.py), [`PaymentProcessor`](src/hotieler/application/ports.py), [`DeterministicPaymentProcessor`](src/hotieler/infrastructure/payments.py), [`build_payment_processors`](src/hotieler/infrastructure/payments.py). | `test_payment_approval_routes_each_method_and_confirms`, `test_every_payment_method_is_registered`. |
| Let provider outcome drive booking state. | **COMPLETE** | `APPROVED` produces `CONFIRMED`; `REJECTED` produces `PAYMENT_FAILED` and releases the hold. | [`PaymentService.process`](src/hotieler/application/services.py), [`Booking.confirm`](src/hotieler/domain/entities.py), [`Booking.mark_payment_failed`](src/hotieler/domain/entities.py), [`MockPaymentOutcome`](src/hotieler/domain/enums.py). | `test_payment_rejection_releases_inventory_immediately`, `test_rejected_payment_is_terminal_and_released_inventory_can_complete_a_new_flow`. |

### 2.5 Cancellation and refund policy

| Brief point | Status | Endpoint and evidence | Exact implementation symbols | Exact tests |
|---|---|---|---|---|
| Cancel an existing pending or confirmed booking. | **COMPLETE** | `POST /api/v1/bookings/{booking_id}/cancel`; repeat the request to observe safe replay. | [`cancel_booking`](src/hotieler/api/routes.py), [`CancellationService.cancel`](src/hotieler/application/services.py), [`Booking.cancel`](src/hotieler/domain/entities.py), [`CancellationRecord`](src/hotieler/domain/entities.py). | `test_confirmed_cancellation_is_repeat_safe_and_releases_inventory`, `test_booking_cancellation_is_repeat_safe_at_entity_boundary`. |
| Apply a simple, replaceable cancellation/refund policy. | **COMPLETE** | Create confirmed bookings at the documented calendar-day boundaries and cancel them. | [`CancellationPolicy`](src/hotieler/domain/policies.py), [`DefaultCancellationPolicy.quote`](src/hotieler/domain/policies.py), [`RefundQuote`](src/hotieler/domain/policies.py). | `test_default_cancellation_policy_boundaries`, `test_http_cancellation_supports_partial_zero_and_rejected_refund_boundaries`, `test_pending_booking_cancellation_requires_no_refund`. |
| Release cancelled inventory for discovery and booking again. | **COMPLETE** | Search before and after cancellation, then create a replacement booking. | [`Booking.reserves_inventory`](src/hotieler/domain/entities.py), [`CancellationService.cancel`](src/hotieler/application/services.py), [`AvailabilitySearchService.search`](src/hotieler/application/services.py). | `test_multi_unit_inventory_is_cumulative_and_released_by_cancellation`, `test_approved_payment_replay_cancellation_and_inventory_release`. |

## 3. Technical requirements and public HTTP contract

| Brief point | Status | Endpoint/artifact | Exact implementation symbols | Exact tests/evidence |
|---|---|---|---|---|
| Required implementation stack. | **PARTIAL** - recruiter-approved exception | Python 3.12/FastAPI replaces Java/Spring under recruiter approval, so this remains a deliberate, externally authorized deviation rather than literal compliance. | [`pyproject.toml`](pyproject.toml), [`Dockerfile`](Dockerfile), [`create_app`](src/hotieler/main.py). | `test_health_and_openapi_expose_the_supported_contract`; external approval correspondence. |
| Expose discovery, onboarding, booking, payment, and cancellation operations. | **COMPLETE** | `POST /owners`, `POST /owners/{id}/properties`, `GET /properties/search`, `POST/GET /bookings`, `POST /payments`, `POST /cancel`, all under `/api/v1`. | Route functions [`create_owner`, `create_property`, `search_properties`, `create_booking`, `get_booking`, `process_payment`, and `cancel_booking`](src/hotieler/api/routes.py). | `test_openapi_schema_exposes_the_complete_public_http_contract`, `test_openapi_groups_operations_by_business_capability`. |
| Keep in-memory persistence behind replaceable repository contracts. | **COMPLETE** | Persistence is internal; no storage-specific shape crosses HTTP. | [`OwnerRepository`, `PropertyRepository`, `BookingRepository`, and `PaymentRepository`](src/hotieler/application/ports.py); implementations in [`infrastructure/repositories.py`](src/hotieler/infrastructure/repositories.py). | `test_booking_repository_copies_on_write_get_and_list`, `test_owner_repository_add_rejects_duplicate_identifier_without_replacement`, `test_property_repository_add_rejects_cross_property_room_identifier_collision_atomically`. |
| Mock third-party payment behavior behind an owned abstraction. | **COMPLETE** | Payment endpoint accepts only a method and deterministic demo outcome. | [`PaymentProcessor`](src/hotieler/application/ports.py), [`DeterministicPaymentProcessor`](src/hotieler/infrastructure/payments.py). | `test_payment_rejects_inconsistent_processor_result_without_mutating_booking`, `test_payment_approval_routes_each_method_and_confirms`. |
| Document build/run instructions and key assumptions. | **COMPLETE** | [`README.md`](README.md) is the evaluator entry point; [`DESIGN.md`](DESIGN.md) is the interview aid. | [`run.sh`](run.sh), [`Makefile`](Makefile), [`compose.yaml`](compose.yaml). | `./run.sh --no-open`, `./run.sh test`, and `make verify` are the documented commands. Final results must be recorded only after execution. |

### Endpoint inventory

| Method | Path | Route symbol | Primary service symbol | Walkthrough role |
|---|---|---|---|---|
| `GET` | `/health` | [`health`](src/hotieler/main.py) | Application readiness | Confirm the process is reachable. |
| `POST` | `/api/v1/owners` | [`create_owner`](src/hotieler/api/routes.py) | [`CatalogService.create_owner`](src/hotieler/application/services.py) | Create an owner. |
| `POST` | `/api/v1/owners/{owner_id}/properties` | [`create_property`](src/hotieler/api/routes.py) | [`CatalogService.create_property`](src/hotieler/application/services.py) | Onboard property plus room types. |
| `GET` | `/api/v1/properties/search` | [`search_properties`](src/hotieler/api/routes.py) | [`AvailabilitySearchService.search`](src/hotieler/application/services.py) | Discover available room options. |
| `POST` | `/api/v1/bookings` | [`create_booking`](src/hotieler/api/routes.py) | [`BookingService.create`](src/hotieler/application/services.py) | Create a pending hold. |
| `GET` | `/api/v1/bookings/{booking_id}` | [`get_booking`](src/hotieler/api/routes.py) | [`BookingService.get`](src/hotieler/application/services.py) | Read authoritative state. |
| `POST` | `/api/v1/bookings/{booking_id}/payments` | [`process_payment`](src/hotieler/api/routes.py) | [`PaymentService.process`](src/hotieler/application/services.py) | Approve/reject and replay a payment. |
| `POST` | `/api/v1/bookings/{booking_id}/cancel` | [`cancel_booking`](src/hotieler/api/routes.py) | [`CancellationService.cancel`](src/hotieler/application/services.py) | Cancel and record refund result. |

## 4. Deliverables

| Deliverable | Status | Evidence |
|---|---|---|
| Runnable backend project with source and dependency/build definition. | **PARTIAL** - recruiter-approved exception | The backend is runnable and complete, but it uses the approved Python/FastAPI substitution rather than the brief's literal Java/Spring build. Evidence: [`src/hotieler/`](src/hotieler), [`pyproject.toml`](pyproject.toml), committed [`uv.lock`](uv.lock), [`Dockerfile`](Dockerfile), and [`compose.yaml`](compose.yaml). |
| README with how to run, design decisions, assumptions, and future work. | **COMPLETE** | [`README.md`](README.md) contains Quick start, request tracing, repository structure, quality gates, assumptions, and production evolution, and points to [`DESIGN.md`](DESIGN.md) for the focused architecture and trade-offs. |
| Unit tests for core business logic. | **COMPLETE** | [`tests/unit/`](tests/unit) covers value objects, entities, policies, services, repositories, locks, and adapters. Representative tests: `test_stay_period_uses_half_open_overlap_semantics`, `test_booking_rejects_illegal_transitions`, `test_standard_pricing_snapshots_nights_rooms_and_rate`, and `test_payment_idempotency_replays_and_rejects_changed_fingerprint`. |

## 5. Evaluation rubric mapping

| Review area | Weight in brief | Status | Concrete evidence |
|---|---:|---|---|
| Design and abstractions | High | **COMPLETE** | Narrow protocols in [`application/ports.py`](src/hotieler/application/ports.py); strategies in [`domain/policies.py`](src/hotieler/domain/policies.py); specifications in [`domain/specifications.py`](src/hotieler/domain/specifications.py); explicit [`build_container`](src/hotieler/container.py). Extension examples are documented in [`DESIGN.md`](DESIGN.md). |
| Domain modelling | High | **COMPLETE** | Immutable [`Money`](src/hotieler/domain/value_objects.py) and [`StayPeriod`](src/hotieler/domain/value_objects.py); [`OwnerAccount`, `Property`, `RoomType`, `Booking`, and `PaymentRecord`](src/hotieler/domain/entities.py); entity-owned transitions and snapshot invariants. Tests: `test_room_type_calculates_ceiling_room_units`, `test_booking_owns_confirmation_and_failed_payment_transitions`, `test_cancellation_refund_cannot_exceed_booking_total`. |
| Code quality | High | **COMPLETE** | Cohesive layers, typed protocols, strict static checks in [`pyproject.toml`](pyproject.toml), formatting/lint/typecheck targets in [`Makefile`](Makefile), and no generic base service/repository hierarchy. Final quality results belong in release evidence only after execution. |
| Correctness | High | **COMPLETE** | Full approved and rejected HTTP journeys, derived inventory, booking-time price/unit snapshots, and deterministic state transitions. Tests: `test_approved_payment_replay_cancellation_and_inventory_release`, `test_rejected_payment_is_terminal_and_released_inventory_can_complete_a_new_flow`, `test_multi_unit_inventory_is_cumulative_and_released_by_cancellation`. |
| Edge cases | Medium | **COMPLETE** | Stable validation/conflict errors, property-room mismatch checks, past/zero-night rejection, half-open adjacency, terminal-state rejection, copy-safe repositories, race tests, and refund boundaries. Tests: `test_transport_and_domain_validation_failures_use_the_same_error_shape`, `test_booking_service_allows_adjacent_and_disjoint_stays_on_one_unit`, `test_terminal_booking_states_reject_payment_with_a_new_key`, `test_payment_and_cancellation_race_reaches_a_valid_terminal_state`. |
| Testing | Medium | **COMPLETE** | [`tests/unit/`](tests/unit), [`tests/integration/`](tests/integration), and [`tests/concurrency/`](tests/concurrency) separate rule, HTTP, and race evidence. Concurrency uses barriers rather than timing sleeps. Run `make verify` to obtain results for the current commit. |

## 6. Bonus requirements

| Bonus | Status | Implementation | Exact tests / Swagger proof |
|---|---|---|---|
| Simultaneous booking protection. | **COMPLETE** | [`BookingService.create`](src/hotieler/application/services.py) locks `room_type:{id}` through authoritative availability calculation and save using [`InMemoryKeyedLockManager`](src/hotieler/infrastructure/locking.py). | `test_simultaneous_booking_attempts_have_exactly_one_winner`, `test_concurrent_two_unit_requests_never_exceed_three_unit_inventory`. |
| Pluggable pricing strategy. | **COMPLETE** | [`PricingStrategy`](src/hotieler/domain/policies.py) and [`StandardPricingStrategy`](src/hotieler/domain/policies.py) are injected into search and booking services. No speculative dynamic rule is included. | `test_standard_pricing_snapshots_nights_rooms_and_rate`, `test_booking_holds_inventory_and_snapshots_quote`. |
| Payment idempotency. | **COMPLETE** | Required `Idempotency-Key`; [`PaymentService.fingerprint`](src/hotieler/application/services.py); idempotency lookup through [`PaymentRepository`](src/hotieler/application/ports.py); idempotency and booking locks use a documented order. | Replay the same Swagger request/key, then change method/outcome with the same key. Tests: `test_concurrent_same_key_payment_is_processed_once_and_replayed`, `test_concurrent_same_key_for_different_bookings_has_one_owner`, `test_payment_replay_after_cancellation_returns_the_original_payment_snapshot`. |
| Basic OpenAPI/Swagger documentation. | **COMPLETE** | `/docs`, `/redoc`, and `/openapi.json`; operation tags/descriptions/examples live in [`api/routes.py`](src/hotieler/api/routes.py) and [`api/schemas.py`](src/hotieler/api/schemas.py). | `test_swagger_ui_is_available_and_uses_the_public_openapi_schema`, `test_openapi_schema_exposes_the_complete_public_http_contract`, `test_openapi_groups_operations_by_business_capability`. |

## 7. Out-of-scope guardrails

These exclusions are positive scope decisions, not missing mandatory work.

| Brief exclusion | Status | Repository decision |
|---|---|---|
| Authentication, authorization, and frontend/UI. | **OUT-OF-SCOPE** | No auth subsystem or frontend exists. Swagger is API documentation, not an application UI. |
| Production database, migrations, and deployment engineering. | **OUT-OF-SCOPE** | State is in memory. Docker and GitHub Actions are evaluator reproducibility tools, not a production deployment claim. There are no ORM models, migrations, Kubernetes resources, or cloud manifests. |
| Exhaustive product feature coverage. | **OUT-OF-SCOPE** | No room numbers, reviews, taxes, coupons, maps, uploads, notifications, analytics, or catalog-editing suite. The project prioritizes the assessed flows. |
| Real third-party payment/refund integration. | **OUT-OF-SCOPE** | [`DeterministicPaymentProcessor`](src/hotieler/infrastructure/payments.py) is the adapter used for all methods. The public API rejects the need for credentials by accepting only a demo outcome. Refunds are policy-calculated records, not external fund movement. |

## 8. Ground rules and submission readiness

| Ground rule | Status | Evidence / responsibility |
|---|---|---|
| Standard development tools and documentation may be used. | **COMPLETE** | The project uses standard Python/FastAPI/Docker tooling declared in the repository. |
| Candidate authorship, walkthrough readiness, trade-off explanation, and live extension readiness. | **PARTIAL** | [`DESIGN.md`](DESIGN.md), the narrow protocols, and the extension examples support the discussion. Authorship and interview readiness are human attestations that source code cannot prove. The candidate should rehearse the demo and be able to implement a new specification, pricing strategy, cancellation policy, or payment adapter. |
| Share the project with its README. | **COMPLETE** | The public repository includes [`README.md`](README.md), this audit index, and the implementation. |

## 9. Reproducible point-to-point walkthrough

Start or reset the Docker runtime first. Startup is deliberately empty: a fresh clone does
not silently create catalog or transaction records.

```bash
./run.sh restart --no-open
```

Either create an owner and property yourself in Swagger, or opt in to local sample data:

```bash
./run.sh seed
```

The seed command uses the real public owner and property APIs; it does not call a private
fixture endpoint or mutate repositories directly. There is no SQL/database seed because
the assessment persistence adapter is intentionally in memory; `./run.sh seed` is a
developer-side API client. Use the identifiers and future-dated values printed by the
command, then open <http://localhost:8000/docs> and continue:

| Step | Operation | Input | Expected evidence |
|---:|---|---|---|
| 1 | `GET /health` | None | Service reports healthy. |
| 2 | `POST /api/v1/owners` and `POST /api/v1/owners/{owner_id}/properties` | Create the catalog manually, or skip these after running `./run.sh seed`. | The returned IDs identify real in-memory domain records created through the public contract. |
| 3 | `GET /api/v1/properties/search` | Use the created city and a valid future stay. | Available room options include capacity, remaining units, nightly rate, and server-calculated quote. |
| 4 | `POST /api/v1/bookings` | Use returned property/room IDs plus the same stay. | A `PENDING_PAYMENT` booking is created; required units and total price are server snapshots. |
| 5A | `POST /api/v1/bookings/{id}/payments` | New `Idempotency-Key`; `CARD` + `APPROVED`. | The booking becomes `CONFIRMED`. Repeating the identical request/key returns the original payment result. |
| 5B | Repeat from a fresh booking | New key; any method + `REJECTED`. | The booking becomes `PAYMENT_FAILED`; an overlapping search shows the released inventory. |
| 6 | `GET /api/v1/bookings/{id}` | Approved booking ID. | The authoritative booking projection contains payment and lifecycle state. |
| 7 | `POST /api/v1/bookings/{id}/cancel` | Approved booking ID. | The booking becomes `CANCELLED` with the applicable refund record. Repeating cancellation returns the recorded result. |
| 8 | `GET /api/v1/properties/search` | Original search. | Cancelled inventory is available for a replacement booking. |
| 9 | Payment conflict proof | Reuse a prior key with changed method/outcome or another booking. | The API returns `409` with the stable `IDEMPOTENCY_KEY_CONFLICT` error envelope. |

For deterministic concurrency evidence, run the dedicated Docker target:

```bash
make test-concurrency
```

## 10. Implementation completion log

This log distinguishes locally verified implementation evidence from remote release
evidence. The current working tree passes the documented Docker verification and smoke
gates; remote CI remains pending until these changes are committed and pushed.

| Hardening item | Current state | Evidence and remaining gate |
|---|---|---|
| Repository `add` contract parity: duplicate owner/property IDs and cross-property room-type collisions fail atomically. | **IMPLEMENTED.** | Covered by `test_owner_repository_add_rejects_duplicate_identifier_without_replacement`, `test_property_repository_add_rejects_duplicate_identifier_without_replacement`, and `test_property_repository_add_rejects_cross_property_room_identifier_collision_atomically`; rerun the current suite for release evidence. |
| Booking transition timestamp monotonicity and cancellation refund amount/currency/percentage/status invariants. | **IMPLEMENTED.** | Covered by `test_booking_payment_transition_timestamp_cannot_move_backwards`, `test_cancellation_timestamp_cannot_predate_current_booking_update`, `test_cancellation_refund_currency_must_match_booking`, `test_cancellation_refund_cannot_exceed_booking_total`, `test_confirmed_cancellation_requires_a_self_consistent_refund_record`, and `test_invalid_cancellation_policy_result_cannot_corrupt_booking_state`; rerun the current suite for release evidence. |
| Structured JSON request logging and response correlation through `X-Request-ID`, without bodies, sensitive headers, or unmatched caller paths. | **IMPLEMENTED.** | [`RequestLoggingMiddleware`](src/hotieler/api/observability.py); coverage is provided by `test_every_response_has_request_id_and_safe_client_id_is_reused`, `test_request_log_is_structured_and_never_contains_body_or_sensitive_headers`, `test_unmatched_route_does_not_log_the_caller_supplied_path`, and `test_business_lifecycle_events_are_safe_and_structured`. |
| Stable, non-leaking JSON envelope for unexpected HTTP `500` responses. | **IMPLEMENTED.** | [`unexpected_error_handler`](src/hotieler/api/error_handlers.py); covered by `test_unhandled_exception_returns_safe_envelope_and_is_correlated`. |
| Explicit OpenAPI operation descriptions, operation-specific conflict examples, approved/rejected/replayed payment examples, and grouped business capabilities. | **IMPLEMENTED.** | [`api/routes.py`](src/hotieler/api/routes.py), [`api/schemas.py`](src/hotieler/api/schemas.py); covered by `test_openapi_schema_exposes_the_complete_public_http_contract`, `test_openapi_groups_operations_by_business_capability`, and the Swagger test. |
| Transport-boundary email validation and INR-only public pricing input. | **IMPLEMENTED.** | [`CreateOwnerRequest`](src/hotieler/api/schemas.py) uses an email-format boundary and [`MoneyInput`](src/hotieler/api/schemas.py) restricts public pricing input to INR; HTTP validation cases cover the boundary. |
| Docker CI runtime smoke: verify empty startup, health/OpenAPI, API-driven local seeding, non-root execution, and one Uvicorn process, then tear down. | **VERIFIED LOCALLY; REMOTE RUN PENDING.** | The current `make smoke` run passed through [`.github/workflows/ci.yml`](.github/workflows/ci.yml), [`Makefile`](Makefile), and [`scripts/docker-smoke.sh`](scripts/docker-smoke.sh). [GitHub Actions run 36242516289](https://github.com/ssinha2103/Hotieler/actions/runs/36242516289) remains evidence only for the earlier `fa4193d` baseline. |
| Refactor `PaymentService` into smaller cohesive helpers without changing lock ordering, replay semantics, or state outcomes; add extension-seam tests. | **IMPLEMENTED.** | [`PaymentService`](src/hotieler/application/services.py); coverage includes the existing payment/concurrency cases plus `test_alternate_pricing_strategy_plugs_into_search_and_booking`, `test_alternate_cancellation_policy_plugs_into_service`, and `test_custom_registered_payment_processor_plugs_into_service`. |
| README refund wording, documentation links, and audit-index discoverability. | **IMPLEMENTED.** | [`README.md`](README.md) links this audit and [`DESIGN.md`](DESIGN.md), distinguishes refund calculation from fund movement, and documents the evaluator workflow. [`DESIGN.md`](DESIGN.md) records the observability and payment-consistency boundaries. |
| Keep transient `/tmp` content and confidential review renders outside Git and Docker build context. | **VERIFIED LOCALLY.** | [`.gitignore`](.gitignore) and [`.dockerignore`](.dockerignore) contain the guardrails; the current staged path, whitespace, confidential-file-name, and common secret-signature checks pass. |

## 11. Intentional limitations and honest production boundary

The following are intentionally not presented as production-ready behavior:

- State and idempotency records are lost when the single API process restarts.
- Inventory locks and repositories are process-local; exactly one Uvicorn worker is part
  of the correctness contract.
- Pending-payment holds do not expire automatically.
- Search scans in-memory objects and has no pagination, index, or multi-instance snapshot
  isolation.
- Payments are synchronous and deterministic; there are no signed webhooks, unknown
  provider outcomes, reconciliation jobs, or external refund execution.
- Refunds are calculated and recorded by policy. They do not move real funds.
- Calendar-date cancellation rules omit property-local time zones and rate-plan-specific
  conditions.
- Authentication, authorization, owner isolation, audit retention, metrics, tracing,
  rate limiting, durable logging, and secret management are outside this assessment slice.
- Docker makes the evaluator workflow reproducible; it is not evidence of a deployment,
  high availability, backup/restore, or disaster-recovery design.

A production evolution would move inventory and idempotency invariants into durable
database transactions, introduce expiring holds, persist payment attempts before dispatch,
consume signed provider callbacks idempotently, reconcile unknown outcomes, and add the
security and operational controls appropriate to the deployment.

## 12. Verification evidence, real gaps, and final checklist

### Current local evidence and remaining release evidence

Do not copy test counts or coverage from an earlier commit. Run each gate against the
release candidate and record the resulting commit SHA, totals, and CI URL in the release
notes or submission message.

| Gate | Current result / evidence source |
|---|---|
| Complete unit, integration, and concurrency test suite | **PASS - 122 tests** in the current `make verify` run. |
| Ruff formatting/lint, mypy, coverage, and bytecode compilation | **PASS - 94.85% branch-aware coverage** in the current `make verify` run. |
| Isolated Docker runtime smoke | **PASS** in the current `make smoke` run, including empty startup followed by API-driven seeding. |
| Empty-start and API-driven sample-data behavior | `./run.sh restart --no-open`, then `./run.sh seed` |
| Live HTTP walkthrough | Swagger approval, rejection, replay, conflict, cancellation, and inventory-release flow |
| Staged publication audit | **PASS** for the current staged diff: whitespace, confidential-file names, and common private-key/token signatures were checked. |
| Remote GitHub Actions | CI run attached to the final pushed commit |

### External and human evidence

These are submission responsibilities that source code and CI cannot establish:

1. Retain the recruiter approval for the Python/FastAPI exception with the submission;
   source code cannot turn the literal Java/Spring requirement into complete compliance.
2. The take-home elapsed-time constraint, candidate authorship, and live interview
   readiness remain human-proven submission facts, not repository-verifiable claims.

Complete the open repository gates with Docker-backed commands:

```bash
docker compose config --quiet
make verify
make smoke
./run.sh restart --no-open
./run.sh seed
./run.sh status
```

After the commands finish, verify in Swagger that approval, rejection, payment replay,
idempotency conflict, cancellation replay, inventory release, and request correlation
behave exactly as documented above. Restart once more and confirm the catalog is empty.
Update this file only with results actually observed on the release commit.
