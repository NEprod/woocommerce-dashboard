"""Fixed operations for the existing authenticated Shopify client; no fulfilment writes."""
SUB_FIELDS='id topic uri apiVersion { handle supported }'
SUBSCRIPTIONS='query TLCOrderSubscriptions($after: String) { webhookSubscriptions(first: 50, after: $after) { nodes { '+SUB_FIELDS+' } pageInfo { hasNextPage endCursor } } }'
SUBSCRIPTION='query TLCOrderSubscription($id: ID!) { webhookSubscription(id: $id) { '+SUB_FIELDS+' } }'
SUB_CREATE='mutation TLCOrderWebhookCreate($topic: WebhookSubscriptionTopic!, $input: WebhookSubscriptionInput!) { webhookSubscriptionCreate(topic: $topic, webhookSubscription: $input) { webhookSubscription { '+SUB_FIELDS+' } userErrors { field message } } }'
SUB_UPDATE='mutation TLCOrderWebhookUpdate($id: ID!, $input: WebhookSubscriptionInput!) { webhookSubscriptionUpdate(id: $id, webhookSubscription: $input) { webhookSubscription { '+SUB_FIELDS+' } userErrors { field message } } }'
SUB_DELETE='mutation TLCOrderWebhookDelete($id: ID!) { webhookSubscriptionDelete(id: $id) { deletedWebhookSubscriptionId userErrors { field message } } }'
STATUS_READ='''query TLCOrderProductionStatus($id: ID!) { shop { id } order(id: $id) { id metafield(namespace: "tlc_operations", key: "production_status") { id namespace key type value compareDigest } } }'''
STATUS_SET='''mutation TLCOrderProductionStatusSet($metafields: [MetafieldsSetInput!]!) { metafieldsSet(metafields: $metafields) { metafields { id namespace key type value compareDigest } userErrors { field message code } } }'''
READ_QUERIES=frozenset({SUBSCRIPTIONS,SUBSCRIPTION,STATUS_READ})
WRITE_QUERIES=frozenset({SUB_CREATE,SUB_UPDATE,SUB_DELETE,STATUS_SET})
