from app.models.roulette_data import (
    color_of,
    column_of,
    dozen_of,
    is_valid_number,
    parity_of,
    range_of,
)


def test_zero_is_green_and_has_no_parity_range_dozen_column():
    assert color_of(0) == "green"
    assert parity_of(0) is None
    assert range_of(0) is None
    assert dozen_of(0) is None
    assert column_of(0) is None


def test_known_red_and_black_numbers():
    assert color_of(1) == "red"
    assert color_of(32) == "red"
    assert color_of(2) == "black"
    assert color_of(35) == "black"


def test_every_nonzero_number_is_red_or_black_never_both():
    for n in range(1, 37):
        assert color_of(n) in ("red", "black")


def test_parity():
    assert parity_of(3) == "odd"
    assert parity_of(4) == "even"


def test_range_boundaries():
    assert range_of(1) == "low"
    assert range_of(18) == "low"
    assert range_of(19) == "high"
    assert range_of(36) == "high"


def test_dozen_boundaries():
    assert dozen_of(1) == 1
    assert dozen_of(12) == 1
    assert dozen_of(13) == 2
    assert dozen_of(24) == 2
    assert dozen_of(25) == 3
    assert dozen_of(36) == 3


def test_column_assignment():
    assert column_of(1) == 1
    assert column_of(2) == 2
    assert column_of(3) == 3
    assert column_of(4) == 1
    assert column_of(36) == 3


def test_is_valid_number():
    assert is_valid_number(0)
    assert is_valid_number(36)
    assert not is_valid_number(37)
    assert not is_valid_number(-1)
    assert not is_valid_number("12")
