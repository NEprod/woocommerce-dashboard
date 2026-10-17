"""Fixed read-only 2026-10 packaging capability query (no shipping writes)."""
CAPABILITY='''query TLCPackagingCapability {
 shop { id myshopifyDomain }
 queryRoot: __type(name: "QueryRoot") { fields { name } }
 mutations: __type(name: "Mutation") { fields { name } }
 measurement: __type(name: "InventoryItemMeasurement") { fields { name } }
 measurementInput: __type(name: "InventoryItemMeasurementInput") { inputFields { name } }
 package: __type(name: "ShippingPackage") { fields { name } }
 shopType: __type(name: "Shop") { fields { name } }
 packageInput: __type(name: "CustomShippingPackageInput") { inputFields { name } }
 packageTypes: __type(name: "ShippingPackageType") { enumValues { name } }
 updatePayload: __type(name: "ShippingPackageUpdatePayload") { fields { name } }
}'''
