"""Ephemeral stock-card barcode geometry, never identity allocation or stock."""
from decimal import Decimal


BARCODE_WIDTH_MM = Decimal('45.212')  # 1.78 inches, INCLUDING both quiet zones.
BARCODE_HEIGHT_MM = Decimal('11.43')  # 0.45 inches.
MIN_MODULE_MM = Decimal('0.20')
QUIET_MODULES = 12


def barcode_geometry(code):
    """Use the same Code128 encoding as inventory labels, with a fixed size floor.

    0.20 mm is our layout floor, not a guarantee for every printer/scanner.
    Never squeeze a long identifier to fit; offer the existing larger label.
    """
    if not code:
        return dict(fits=False, warning='Permanent barcode missing. No barcode has been generated.')
    from barcode import Code128
    from barcode.errors import BarcodeError
    try:
        modules = len(Code128(code).build()[0])
    except (BarcodeError, ValueError):
        return dict(fits=False, warning='The saved identifier cannot be rendered as Code 128. The identifier has not been changed.')
    module = BARCODE_WIDTH_MM / (modules + QUIET_MODULES * 2)
    if module < MIN_MODULE_MM:
        return dict(fits=False, warning='Identifier too long for a reliably sized barcode on this 2 × 3.5-inch card. Use the larger inventory label; the identifier has not been shortened.')
    return dict(fits=True, warning=None, module_width=float(module),
                quiet_zone=float(module * QUIET_MODULES), module_height=float(BARCODE_HEIGHT_MM),
                margin_top=0, margin_bottom=0, write_text=False)
