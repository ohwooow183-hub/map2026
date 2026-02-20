import pytest
from rosreestr2coord.utils import validate_code


class TestValidateCode:
    """Tests for cadastral number validation."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "code",
        [
            "50:20:0010203:456",    # full parcel number
            "38:06:144003:4723",    # full with leading zeros
            "77:01:0001234:99",     # Moscow
            "02:02:0000000:1",      # single-digit parcel
            "50:20:0010203",        # quarter
            "50:20",                # district
            "50",                   # region
            "1:1:10000:1",          # minimal digits
            "99:99:9999999:99999",  # max region/district, long parcel
        ],
    )
    def test_valid_codes(self, code):
        validate_code(code)  # should not raise

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "code",
        [
            "",                         # empty
            "abc",                      # letters
            "50-20-0010203-456",        # wrong delimiter
            "50:20:0010203:456:789",    # too many parts
            "123:20:0010203:456",       # region > 2 digits
            "50:123:0010203:456",       # district > 2 digits
            "50:20:1234:456",           # quarter too short (< 5 digits)
            "50:20:12345678:456",       # quarter too long (> 7 digits)
            "50:20:00102AB:456",        # non-digits in quarter
            ":20:0010203:456",          # empty region
            "50:20:0010203:",           # empty parcel
        ],
    )
    def test_invalid_codes(self, code):
        with pytest.raises(ValueError, match="Invalid cadastral code format"):
            validate_code(code)

    @pytest.mark.unit
    def test_none_input(self):
        with pytest.raises(ValueError, match="non-empty string"):
            validate_code(None)

    @pytest.mark.unit
    def test_whitespace_stripped(self):
        validate_code("  50:20:0010203:456  ")  # should not raise


@pytest.mark.unit
def test_area_rejects_invalid_code():
    """Area.__init__ should raise ValueError for invalid cadastral codes."""
    from rosreestr2coord.parser import Area

    with pytest.raises(ValueError, match="Invalid cadastral code"):
        Area("not-a-valid-code", area_type=1, with_log=False)
