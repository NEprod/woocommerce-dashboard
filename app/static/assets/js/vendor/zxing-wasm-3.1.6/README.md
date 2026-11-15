# ZXing WASM reader

The two JavaScript files in `dist/es/` and the reader binary in `dist/reader/`
are unmodified from `npm pack zxing-wasm@3.1.6` ([upstream](https://github.com/Sec-ant/zxing-wasm)).
The module is loaded only if the browser is missing native support for a scanner
format. `inventory-barcode-fallback.mjs` resolves the WASM URL locally; the
storefront, an external service, and a CDN are not involved.

- npm integrity: `sha512-mU7UNuvl+hOT+nlSOQTMHZ2tM8lRMsbjDIp5IO/+HmBl/FOq77q0OSZPJ3cm42A9c1MKU6Uve67FiGdkhsrT9g==`
- `zxing_reader.wasm` SHA-256: `5003fb4b88c4fbd1fd518a2fd8c8ad365c519e183a08c03a322dddfea3cea84e`
- ZXing WASM: MIT (`LICENSE`); ZXing-C++ (`2c3dcfeffa7878950a58c1703c86774a428b0c91`) and the WASM C++ bindings: Apache 2.0 (`LICENSE.zxing-cpp`).

To update, use the JS and WASM from the same release and run
`node --test tests/javascript/inventory-scan.test.mjs`.
