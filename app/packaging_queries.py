"""Fixed read-only 2026-10 packaging capability query (no shipping writes)."""
CAPABILITY='''query TLCPackagingCapability {
 shop { id plan { displayName } }
 queryRoot: __type(name: "QueryRoot") { fields { name } }
 mutations: __type(name: "Mutation") { fields { name } }
 measurement: __type(name: "InventoryItemMeasurement") { fields { name } }
 measurementInput: __type(name: "InventoryItemMeasurementInput") { inputFields { name } }
 package: __type(name: "ShippingPackage") { fields { name } }
}'''
