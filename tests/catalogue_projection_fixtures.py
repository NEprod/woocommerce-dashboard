"""Manual SQL catalogue fixtures need the same derived views as completed scans."""
def project_existing_sources():
    from app import db
    from app.models import Product
    from app.automatic_publishing import queue_product
    from app.catalogue_views import capture, context_digest
    context = context_digest()
    for product in Product.query.all():
        queue_product(product)
        capture(product, context=context)
    db.session.commit()
