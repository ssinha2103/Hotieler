from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest

from hotieler.domain.entities import (
    Booking,
    CancellationRecord,
    OwnerAccount,
    PaymentRecord,
    Property,
    RoomType,
)
from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from hotieler.domain.errors import (
    CancellationNotAllowedError,
    DomainValidationError,
    InvalidBookingTransitionError,
)
from hotieler.domain.policies import DefaultCancellationPolicy, StandardPricingStrategy
from hotieler.domain.value_objects import Money, StayPeriod

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)


def booking(
    *, check_in: date = date(2030, 1, 10), status: BookingStatus = BookingStatus.PENDING_PAYMENT
) -> Booking:
    created = Booking(
        id=UUID(int=1),
        property_id=UUID(int=2),
        room_type_id=UUID(int=3),
        stay=StayPeriod(check_in, date.fromordinal(check_in.toordinal() + 2)),
        guest_count=3,
        required_units=2,
        total_price=Money(Decimal("4000")),
        created_at=NOW,
        updated_at=NOW,
    )
    if status is BookingStatus.CONFIRMED:
        created.confirm(UUID(int=4), NOW)
    elif status is BookingStatus.PAYMENT_FAILED:
        created.mark_payment_failed(UUID(int=4), NOW)
    return created


def payment_record(**overrides: Any) -> PaymentRecord:
    values: dict[str, Any] = {
        "id": UUID(int=10),
        "booking_id": UUID(int=1),
        "method": PaymentMethod.CARD,
        "amount": Money(Decimal("4000")),
        "status": PaymentStatus.APPROVED,
        "mock_outcome": MockPaymentOutcome.APPROVED,
        "provider_reference": "provider-10",
        "idempotency_key": "payment-key",
        "fingerprint": "payment-fingerprint",
        "booking_status_after": BookingStatus.CONFIRMED,
        "created_at": NOW,
    }
    values.update(overrides)
    return PaymentRecord(**values)


def confirmed_cancelled_booking(
    cancelled_at: datetime = NOW,
) -> Booking:
    value = booking(status=BookingStatus.CONFIRMED)
    value.cancel(
        CancellationRecord(
            cancelled_at=cancelled_at,
            refund_amount=Money(Decimal("4000")),
            refund_percentage=Decimal("100"),
            refund_status=RefundStatus.CALCULATED,
        )
    )
    return value


def room_type(**overrides: Any) -> RoomType:
    values: dict[str, Any] = {
        "id": UUID(int=20),
        "property_id": UUID(int=21),
        "name": " Deluxe ",
        "total_units": 3,
        "guests_per_unit": 2,
        "nightly_rate": Money(Decimal("1500")),
        "amenities": frozenset({" WiFi ", " ", "POOL"}),
    }
    values.update(overrides)
    return RoomType(**values)


def property_entity(**overrides: Any) -> Property:
    property_id = overrides.get("id", UUID(int=21))
    values: dict[str, Any] = {
        "id": property_id,
        "owner_id": UUID(int=22),
        "name": " Forest House ",
        "city": " Bengaluru ",
        "locality": " Indiranagar ",
        "address": " 1 Residency Road ",
        "star_rating": Decimal("4.5"),
        "amenities": frozenset({" Parking ", "WIFI"}),
        "room_types": (room_type(property_id=property_id),),
        "created_at": NOW,
    }
    values.update(overrides)
    return Property(**values)


def test_owner_normalizes_text_and_email() -> None:
    owner = OwnerAccount(
        id=UUID(int=1),
        name=" Forest Hospitality ",
        contact_email=" OWNER@Example.COM ",
        created_at=NOW,
    )

    assert owner.name == "Forest Hospitality"
    assert owner.contact_email == "owner@example.com"
    assert owner.created_at == NOW


def test_owner_rejects_blank_name() -> None:
    with pytest.raises(DomainValidationError, match="Owner name cannot be blank"):
        OwnerAccount(
            id=UUID(int=1),
            name="  ",
            contact_email="owner@example.com",
            created_at=NOW,
        )


@pytest.mark.parametrize("email", ["owner.example.com", "@example.com", "owner@"])
def test_owner_rejects_malformed_email(email: str) -> None:
    with pytest.raises(DomainValidationError, match="Contact email must be valid"):
        OwnerAccount(
            id=UUID(int=1),
            name="Forest Hospitality",
            contact_email=email,
            created_at=NOW,
        )


def test_room_type_calculates_ceiling_room_units() -> None:
    room = RoomType(
        id=UUID(int=1),
        property_id=UUID(int=2),
        name="Deluxe",
        total_units=5,
        guests_per_unit=2,
        nightly_rate=Money(Decimal("1000")),
        amenities=frozenset({" WiFi ", "wifi", "Pool"}),
    )

    assert room.units_for(1) == 1
    assert room.units_for(2) == 1
    assert room.units_for(3) == 2
    assert room.amenities == frozenset({"wifi", "pool"})


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("total_units", 0, "at least one unit"),
        ("guests_per_unit", 0, "capacity must be greater than zero"),
    ],
)
def test_room_type_rejects_non_positive_inventory_configuration(
    field_name: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        room_type(**{field_name: value})


def test_room_type_rejects_non_positive_guest_request() -> None:
    room = room_type()

    with pytest.raises(DomainValidationError, match="Guest count must be greater than zero"):
        room.units_for(0)


def test_room_type_requires_a_chargeable_money_nightly_rate() -> None:
    with pytest.raises(DomainValidationError, match="money value"):
        room_type(nightly_rate="100.00")  # type: ignore[arg-type]

    with pytest.raises(DomainValidationError, match="at least 0.01"):
        room_type(nightly_rate=Money.zero())


def test_property_normalizes_fields_and_finds_its_room_type() -> None:
    value = property_entity(star_rating="4.5")

    assert value.name == "Forest House"
    assert value.city == "Bengaluru"
    assert value.locality == "Indiranagar"
    assert value.address == "1 Residency Road"
    assert value.star_rating == Decimal("4.5")
    assert value.amenities == frozenset({"parking", "wifi"})
    assert value.find_room_type(UUID(int=20)) == value.room_types[0]
    assert value.find_room_type(UUID(int=999)) is None


def test_property_rejects_non_numeric_star_rating() -> None:
    with pytest.raises(DomainValidationError, match="Star rating must be numeric"):
        property_entity(star_rating="not-a-rating")


def test_property_enforces_effective_star_rating_scale() -> None:
    assert property_entity(star_rating=Decimal("4.5000")).star_rating == Decimal("4.5000")

    with pytest.raises(DomainValidationError, match="at most one decimal place"):
        property_entity(star_rating=Decimal("4.5001"))


@pytest.mark.parametrize("rating", [Decimal("NaN"), Decimal("0.9"), Decimal("5.1")])
def test_property_rejects_non_finite_or_out_of_range_star_rating(rating: Decimal) -> None:
    with pytest.raises(DomainValidationError, match="Star rating must be between 1 and 5"):
        property_entity(star_rating=rating)


def test_property_requires_at_least_one_room_type() -> None:
    with pytest.raises(DomainValidationError, match="at least one room type"):
        property_entity(room_types=())


def test_property_rejects_room_type_owned_by_another_property() -> None:
    with pytest.raises(DomainValidationError, match="must belong to its property"):
        property_entity(room_types=(room_type(property_id=UUID(int=999)),))


def test_property_rejects_duplicate_room_type_identifiers() -> None:
    duplicate_id = UUID(int=20)
    with pytest.raises(DomainValidationError, match="identifiers must be unique"):
        property_entity(
            room_types=(room_type(id=duplicate_id), room_type(id=duplicate_id, name="Suite"))
        )


@pytest.mark.parametrize(
    "name",
    ["Forest\x00House", "Forest\x1fHouse", "Forest\x7fHouse", "Forest\x9fHouse"],
)
def test_human_readable_entity_text_rejects_control_characters(name: str) -> None:
    with pytest.raises(DomainValidationError, match="cannot contain control characters"):
        OwnerAccount(
            id=UUID(int=1),
            name=name,
            contact_email="owner@example.com",
            created_at=NOW,
        )


def test_booking_direct_construction_requires_booking_status_enum() -> None:
    value = booking()

    with pytest.raises(DomainValidationError, match="status must be a BookingStatus value"):
        replace(value, status=cast(BookingStatus, "BOGUS"))


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("guest_count", 0, "Guest count must be greater than zero"),
        ("required_units", 0, "Required room units must be greater than zero"),
    ],
)
def test_booking_rejects_non_positive_capacity_snapshot(
    field_name: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        replace(booking(), **{field_name: value})


def test_booking_rejects_update_timestamp_before_creation() -> None:
    with pytest.raises(DomainValidationError, match="updated_at cannot be earlier than created_at"):
        replace(booking(), updated_at=NOW - timedelta(microseconds=1))


def test_booking_rejects_malformed_cancellation_record() -> None:
    with pytest.raises(DomainValidationError, match="cancellation must be a CancellationRecord"):
        replace(booking(), cancellation=cast(CancellationRecord, "not-a-record"))


@pytest.mark.parametrize("status", [BookingStatus.CONFIRMED, BookingStatus.PAYMENT_FAILED])
def test_processed_booking_reconstruction_requires_payment_identifier(
    status: BookingStatus,
) -> None:
    with pytest.raises(DomainValidationError, match="requires a payment identifier"):
        replace(booking(), status=status)


def test_pending_booking_reconstruction_rejects_payment_identifier() -> None:
    with pytest.raises(DomainValidationError, match="pending booking cannot have"):
        replace(booking(), payment_id=UUID(int=4))


def test_cancelled_booking_reconstruction_requires_cancellation_details() -> None:
    with pytest.raises(DomainValidationError, match="requires cancellation details"):
        replace(booking(), status=BookingStatus.CANCELLED)


def test_non_cancelled_booking_reconstruction_rejects_cancellation_details() -> None:
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money.zero(),
        refund_percentage=Decimal("0"),
        refund_status=RefundStatus.NOT_REQUIRED,
    )

    with pytest.raises(DomainValidationError, match="Only a cancelled booking"):
        replace(
            booking(),
            status=BookingStatus.CONFIRMED,
            payment_id=UUID(int=4),
            cancellation=cancellation,
        )


def test_cancellation_record_requires_refund_status_enum() -> None:
    with pytest.raises(
        DomainValidationError,
        match="refund_status must be a RefundStatus value",
    ):
        CancellationRecord(
            cancelled_at=NOW,
            refund_amount=Money.zero(),
            refund_percentage=Decimal("0"),
            refund_status=cast(RefundStatus, "NOT_REQUIRED"),
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "enum_name"),
    [
        ("method", "CRYPTO", "PaymentMethod"),
        ("status", "PENDING", "PaymentStatus"),
        ("mock_outcome", "TIMEOUT", "MockPaymentOutcome"),
        ("booking_status_after", "PAID", "BookingStatus"),
    ],
)
def test_payment_record_requires_enum_instances(
    field_name: str,
    invalid_value: str,
    enum_name: str,
) -> None:
    with pytest.raises(
        DomainValidationError,
        match=rf"{field_name} must be a {enum_name} value",
    ):
        payment_record(**{field_name: invalid_value})


@pytest.mark.parametrize(
    ("outcome", "payment_status", "booking_status"),
    [
        (MockPaymentOutcome.APPROVED, PaymentStatus.APPROVED, BookingStatus.CONFIRMED),
        (
            MockPaymentOutcome.REJECTED,
            PaymentStatus.REJECTED,
            BookingStatus.PAYMENT_FAILED,
        ),
    ],
)
def test_payment_record_accepts_consistent_outcome_and_normalizes_references(
    outcome: MockPaymentOutcome,
    payment_status: PaymentStatus,
    booking_status: BookingStatus,
) -> None:
    record = payment_record(
        mock_outcome=outcome,
        status=payment_status,
        booking_status_after=booking_status,
        idempotency_key=" payment-key ",
        fingerprint=" payment-fingerprint ",
        provider_reference=" provider-10 ",
    )

    assert record.idempotency_key == "payment-key"
    assert record.fingerprint == "payment-fingerprint"
    assert record.provider_reference == "provider-10"
    assert record.created_at == NOW


@pytest.mark.parametrize(
    "overrides",
    [
        {"mock_outcome": MockPaymentOutcome.REJECTED},
        {"booking_status_after": BookingStatus.PAYMENT_FAILED},
    ],
    ids=["payment-outcome-mismatch", "booking-status-mismatch"],
)
def test_payment_record_rejects_inconsistent_lifecycle_projection(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(DomainValidationError, match="Payment outcome.*disagree"):
        payment_record(**overrides)


@pytest.mark.parametrize(
    "field_name",
    ["idempotency_key", "fingerprint", "provider_reference"],
)
def test_payment_record_rejects_blank_external_reference_fields(field_name: str) -> None:
    with pytest.raises(DomainValidationError, match="cannot be blank"):
        payment_record(**{field_name: "  "})


def test_booking_owns_confirmation_and_failed_payment_transitions() -> None:
    confirmed = booking()
    failed = booking()

    confirmed.confirm(UUID(int=8), NOW)
    failed.mark_payment_failed(UUID(int=9), NOW)

    assert confirmed.status is BookingStatus.CONFIRMED
    assert confirmed.reserves_inventory
    assert failed.status is BookingStatus.PAYMENT_FAILED
    assert not failed.reserves_inventory


@pytest.mark.parametrize("transition", ["confirm", "mark_payment_failed"])
@pytest.mark.parametrize("payment_id", [None, "not-a-uuid"])
def test_booking_payment_transitions_require_uuid_without_mutation(
    transition: str,
    payment_id: object,
) -> None:
    value = booking()

    with pytest.raises(DomainValidationError, match="identifier must be a UUID"):
        getattr(value, transition)(payment_id, NOW)

    assert value.status is BookingStatus.PENDING_PAYMENT
    assert value.payment_id is None
    assert value.updated_at == NOW


@pytest.mark.parametrize("transition", ["confirm", "mark_payment_failed"])
def test_booking_payment_transition_timestamp_cannot_move_backwards(transition: str) -> None:
    value = booking()

    with pytest.raises(
        DomainValidationError,
        match="occurred_at cannot be earlier than updated_at",
    ):
        getattr(value, transition)(UUID(int=8), NOW - timedelta(microseconds=1))

    assert value.status is BookingStatus.PENDING_PAYMENT
    assert value.payment_id is None
    assert value.updated_at == NOW


def test_booking_rejects_illegal_transitions() -> None:
    value = booking(status=BookingStatus.CONFIRMED)

    with pytest.raises(InvalidBookingTransitionError):
        value.confirm(UUID(int=5), NOW)
    with pytest.raises(InvalidBookingTransitionError):
        value.mark_payment_failed(UUID(int=5), NOW)


def test_booking_cancellation_is_repeat_safe_at_entity_boundary() -> None:
    value = booking(status=BookingStatus.CONFIRMED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money(Decimal("4000")),
        refund_percentage=Decimal("100"),
        refund_status=RefundStatus.CALCULATED,
    )

    first = value.cancel(cancellation)
    second = value.cancel(cancellation)

    assert first == second
    assert value.status is BookingStatus.CANCELLED
    assert not value.reserves_inventory


def test_cancel_defensively_rejects_corrupted_cancelled_booking() -> None:
    value = booking()
    value.status = BookingStatus.CANCELLED
    value.cancellation = None
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money.zero(),
        refund_percentage=Decimal("0"),
        refund_status=RefundStatus.NOT_REQUIRED,
    )

    with pytest.raises(InvalidBookingTransitionError):
        value.cancel(cancellation)


def test_reconstructed_cancelled_booking_with_refund_requires_payment_id() -> None:
    value = confirmed_cancelled_booking()

    with pytest.raises(
        DomainValidationError,
        match="calculated refund requires a payment identifier",
    ):
        replace(value, payment_id=None)


def test_reconstructed_booking_rejects_malformed_payment_id() -> None:
    value = confirmed_cancelled_booking()

    with pytest.raises(DomainValidationError, match="Payment identifier must be a UUID"):
        replace(value, payment_id=cast(UUID, "not-a-uuid"))


def test_reconstructed_cancellation_timestamp_must_equal_booking_update() -> None:
    value = confirmed_cancelled_booking(NOW + timedelta(hours=2))

    with pytest.raises(
        DomainValidationError,
        match="updated_at must equal cancelled_at",
    ):
        replace(value, updated_at=NOW + timedelta(hours=1))


def test_reconstructed_pending_cancellation_accepts_no_refund_without_payment() -> None:
    value = booking()
    value.cancel(
        CancellationRecord(
            cancelled_at=NOW,
            refund_amount=Money.zero(),
            refund_percentage=Decimal("0"),
            refund_status=RefundStatus.NOT_REQUIRED,
        )
    )

    reconstructed = replace(value)

    assert reconstructed.status is BookingStatus.CANCELLED
    assert reconstructed.payment_id is None
    assert reconstructed.cancellation is not None


@pytest.mark.parametrize(
    "percentage",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_cancellation_record_rejects_non_finite_refund_percentage(
    percentage: Decimal,
) -> None:
    with pytest.raises(DomainValidationError, match="percentage must be finite"):
        CancellationRecord(
            cancelled_at=NOW,
            refund_amount=Money.zero(),
            refund_percentage=percentage,
            refund_status=RefundStatus.NOT_REQUIRED,
        )


def test_cancellation_record_rejects_non_numeric_refund_percentage() -> None:
    with pytest.raises(DomainValidationError, match="Refund percentage must be numeric"):
        CancellationRecord(
            cancelled_at=NOW,
            refund_amount=Money.zero(),
            refund_percentage=cast(Decimal, "not-a-percentage"),
            refund_status=RefundStatus.NOT_REQUIRED,
        )


@pytest.mark.parametrize("percentage", [Decimal("-0.01"), Decimal("100.01")])
def test_cancellation_record_rejects_out_of_range_refund_percentage(
    percentage: Decimal,
) -> None:
    with pytest.raises(DomainValidationError, match="percentage must be between 0 and 100"):
        CancellationRecord(
            cancelled_at=NOW,
            refund_amount=Money.zero(),
            refund_percentage=percentage,
            refund_status=RefundStatus.NOT_REQUIRED,
        )


def test_cancellation_timestamp_cannot_predate_current_booking_update() -> None:
    value = booking()
    value.confirm(UUID(int=4), NOW + timedelta(hours=2))
    cancellation = CancellationRecord(
        cancelled_at=NOW + timedelta(hours=1),
        refund_amount=value.total_price,
        refund_percentage=Decimal("100"),
        refund_status=RefundStatus.CALCULATED,
    )

    with pytest.raises(
        DomainValidationError,
        match="cancelled_at cannot be earlier than updated_at",
    ):
        value.cancel(cancellation)

    assert value.status is BookingStatus.CONFIRMED
    assert value.cancellation is None
    assert value.updated_at == NOW + timedelta(hours=2)


def test_cancellation_timestamp_cannot_predate_booking_creation() -> None:
    value = booking()
    cancellation = CancellationRecord(
        cancelled_at=NOW - timedelta(microseconds=1),
        refund_amount=Money.zero(),
        refund_percentage=Decimal("0"),
        refund_status=RefundStatus.NOT_REQUIRED,
    )

    with pytest.raises(
        DomainValidationError,
        match="cancelled_at cannot be earlier than updated_at",
    ):
        value.cancel(cancellation)

    assert value.status is BookingStatus.PENDING_PAYMENT
    assert value.cancellation is None
    assert value.updated_at == NOW


def test_cancellation_refund_currency_must_match_booking() -> None:
    value = booking(status=BookingStatus.CONFIRMED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money(Decimal("4000"), "USD"),
        refund_percentage=Decimal("100"),
        refund_status=RefundStatus.CALCULATED,
    )

    with pytest.raises(DomainValidationError, match="same currency"):
        value.cancel(cancellation)

    assert value.status is BookingStatus.CONFIRMED
    assert value.cancellation is None


def test_cancellation_refund_cannot_exceed_booking_total() -> None:
    value = booking(status=BookingStatus.CONFIRMED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money(Decimal("4000.01")),
        refund_percentage=Decimal("100"),
        refund_status=RefundStatus.CALCULATED,
    )

    with pytest.raises(DomainValidationError, match="cannot exceed the booking total price"):
        value.cancel(cancellation)

    assert value.status is BookingStatus.CONFIRMED
    assert value.cancellation is None


@pytest.mark.parametrize(
    ("refund_amount", "refund_percentage", "refund_status", "message"),
    [
        (
            Money.zero(),
            Decimal("100"),
            RefundStatus.CALCULATED,
            "amount must match",
        ),
        (
            Money(Decimal("2000")),
            Decimal("50"),
            RefundStatus.NOT_REQUIRED,
            "status must match",
        ),
    ],
)
def test_confirmed_cancellation_requires_a_self_consistent_refund_record(
    refund_amount: Money,
    refund_percentage: Decimal,
    refund_status: RefundStatus,
    message: str,
) -> None:
    value = booking(status=BookingStatus.CONFIRMED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=refund_amount,
        refund_percentage=refund_percentage,
        refund_status=refund_status,
    )

    with pytest.raises(DomainValidationError, match=message):
        value.cancel(cancellation)

    assert value.status is BookingStatus.CONFIRMED
    assert value.cancellation is None


@pytest.mark.parametrize(
    ("refund_amount", "refund_percentage", "refund_status"),
    [
        (Money(Decimal("1")), Decimal("0"), RefundStatus.NOT_REQUIRED),
        (Money.zero(), Decimal("1"), RefundStatus.NOT_REQUIRED),
        (Money.zero(), Decimal("0"), RefundStatus.CALCULATED),
    ],
)
def test_pending_payment_cancellation_requires_zero_refund_not_required(
    refund_amount: Money,
    refund_percentage: Decimal,
    refund_status: RefundStatus,
) -> None:
    value = booking()
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=refund_amount,
        refund_percentage=refund_percentage,
        refund_status=refund_status,
    )

    with pytest.raises(
        DomainValidationError,
        match="zero refund marked NOT_REQUIRED",
    ):
        value.cancel(cancellation)

    assert value.status is BookingStatus.PENDING_PAYMENT
    assert value.cancellation is None


def test_pending_payment_cancellation_accepts_zero_refund_not_required() -> None:
    value = booking()
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money.zero(),
        refund_percentage=Decimal("0"),
        refund_status=RefundStatus.NOT_REQUIRED,
    )

    result = value.cancel(cancellation)

    assert result is cancellation
    assert value.status is BookingStatus.CANCELLED
    assert value.updated_at == NOW


def test_payment_failed_booking_cannot_be_cancelled() -> None:
    value = booking(status=BookingStatus.PAYMENT_FAILED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money.zero(),
        refund_percentage=Decimal("0"),
        refund_status=RefundStatus.NOT_REQUIRED,
    )

    with pytest.raises(InvalidBookingTransitionError):
        value.cancel(cancellation)


def test_standard_pricing_snapshots_nights_rooms_and_rate() -> None:
    price = StandardPricingStrategy().calculate(
        StayPeriod(date(2030, 1, 10), date(2030, 1, 13)),
        2,
        Money(Decimal("1250.50")),
    )

    assert price == Money(Decimal("7503.00"))


@pytest.mark.parametrize(
    ("on_date", "expected_percent", "expected_amount", "expected_status"),
    [
        (date(2030, 1, 8), Decimal("100"), Decimal("4000.00"), RefundStatus.CALCULATED),
        (date(2030, 1, 9), Decimal("50"), Decimal("2000.00"), RefundStatus.CALCULATED),
        (date(2030, 1, 10), Decimal("0"), Decimal("0.00"), RefundStatus.NOT_REQUIRED),
    ],
)
def test_default_cancellation_policy_boundaries(
    on_date: date,
    expected_percent: Decimal,
    expected_amount: Decimal,
    expected_status: RefundStatus,
) -> None:
    quote = DefaultCancellationPolicy().quote(booking(status=BookingStatus.CONFIRMED), on_date)

    assert quote.percentage == expected_percent
    assert quote.amount.amount == expected_amount
    assert quote.status is expected_status


def test_pending_booking_cancellation_requires_no_refund() -> None:
    quote = DefaultCancellationPolicy().quote(booking(), date(2030, 1, 8))

    assert quote.amount == Money.zero()
    assert quote.status is RefundStatus.NOT_REQUIRED


def test_cancellation_after_check_in_is_rejected() -> None:
    with pytest.raises(CancellationNotAllowedError):
        DefaultCancellationPolicy().quote(
            booking(status=BookingStatus.CONFIRMED), date(2030, 1, 11)
        )


def test_entities_require_timezone_aware_timestamps() -> None:
    with pytest.raises(DomainValidationError):
        Booking(
            id=UUID(int=1),
            property_id=UUID(int=2),
            room_type_id=UUID(int=3),
            stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 11)),
            guest_count=1,
            required_units=1,
            total_price=Money(Decimal("100")),
            created_at=datetime(2030, 1, 1),
            updated_at=datetime(2030, 1, 1),
        )
