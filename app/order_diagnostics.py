"""Fixed safe diagnostic categories; no remote/customer text is retained."""
MESSAGES={
 'configuration':'Shopify credentials/domain are missing, conflicting or invalid. Check Connection settings; do not share credentials.',
 'scope_missing':'Current token lacks read_orders. Approve the existing app installation permission, restart to refresh its token, then Sync Now.',
 'authentication':'Shopify authentication failed. Verify existing credentials, app installation and same-organisation eligibility.',
 'access_denied':'Shopify denied access. Verify installation authorisation and granted scopes; no missing permission is assumed approved.',
 'customer_data_denied':'Shopify denied protected customer data. Review the existing app customer-data approval and installation authorisation.',
 'schema':'Shopify rejected a requested GraphQL field/type or API version. Inspect the pinned 2026-10 query contract.',
 'rate_limited':'Shopify rate limit reached. Saved progress remains; retry the authorised read after capacity returns.',
 'network':'Shopify could not be reached reliably. Check network/domain; previous orders and checkpoint remain.',
 'pagination':'Order pagination is incomplete or did not advance. Saved page/cursor retained; retry Sync Now.',
 'invalid_response':'Shopify order evidence was unavailable, inconsistent or incomplete. No complete snapshot was replaced.',
 'persistence':'Local order persistence failed. Check appdata storage/permissions and database health before retrying.',
 'stale_response':'An older response was rejected; newer order evidence is preserved.',
 'unexpected':'Orders import stopped safely. Check connection and retained task phase before explicit retry.'}


def graphql_category(errors):
    errors=errors if isinstance(errors,list) else []
    codes={str((e.get('extensions') or {}).get('code','')).upper() for e in errors if isinstance(e,dict)}
    messages=' '.join(str(e.get('message','')).lower() for e in errors if isinstance(e,dict))
    if any(term in messages for term in ('protected customer','protected_customer','protected-customer')):return 'customer_data_denied'
    if codes & {'UNDEFINEDFIELD','UNDEFINEDTYPE','GRAPHQL_VALIDATION_FAILED','ARGUMENTNOTACCEPTED','VARIABLENOTUSED'} or 'doesn\'t exist on type' in messages or 'cannot query field' in messages:return 'schema'
    if 'ACCESS_DENIED' in codes:return 'access_denied'
    return 'invalid_response'


def diagnostic(error,phase):
    from sqlalchemy.exc import SQLAlchemyError
    from app.shopify_orders import OrdersPermissionRequired, OrdersImportLimit
    category=getattr(error,'category',None)
    if isinstance(error,SQLAlchemyError):category='persistence'
    elif isinstance(error,OrdersPermissionRequired):category='scope_missing'
    elif isinstance(error,OrdersImportLimit):category='pagination'
    elif phase=='configuration':category='configuration'
    if category not in MESSAGES:category='invalid_response' if phase in {'index','order_read','persist'} else 'unexpected'
    return {'category':category,'phase':phase,'next_action':MESSAGES[category]}
