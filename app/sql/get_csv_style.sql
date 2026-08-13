WITH
P AS (
  SELECT
    'parent' AS row_type,
    -- CSV-like fields
    CASE WHEN EXISTS (SELECT 1 FROM variation v WHERE v.product_id = p.id)
         THEN 'variable' ELSE 'simple' END     AS Type,
    p.sku                                      AS SKU,
    NULL                                       AS Parent,
    p.title                                    AS Name,
    p.regular_price,
    p.sale_price,
    p.sale_start,
    p.sale_end,
    p.weight, p.length, p.width, p.height,
    /* product images */
    (
      SELECT GROUP_CONCAT(s.url, ', ')
      FROM (SELECT DISTINCT pi.url AS url
            FROM product_image pi
            WHERE pi.product_id = p.id) AS s
    ) AS Images,
    /* aggregated attributes for variable parents */
    CASE
      WHEN EXISTS (SELECT 1 FROM variation v WHERE v.product_id = p.id) THEN (
        SELECT GROUP_CONCAT(rowtxt, ' | ')
        FROM (
          SELECT
            va_name || '=' ||
            (
              SELECT GROUP_CONCAT(valtxt, ', ')
              FROM (
                SELECT DISTINCT va2.value AS valtxt
                FROM variation v2
                JOIN variation_attribute va2 ON va2.variation_id = v2.id
                WHERE v2.product_id = p.id
                  AND va2.name = va_name
              )
            ) AS rowtxt
          FROM (
            SELECT DISTINCT va.name AS va_name
            FROM variation v
            JOIN variation_attribute va ON va.variation_id = v.id
            WHERE v.product_id = p.id
          )
        )
      )
      ELSE NULL
    END AS Attributes
  FROM product p
),
V AS (
  SELECT
    'variation'                                AS row_type,
    'variation'                                AS Type,
    v.sku                                      AS SKU,
    p.sku                                      AS Parent,
    p.title                                    AS Name,
    /* effective values: variation override -> product fallback */
    COALESCE(v.regular_price, p.regular_price) AS regular_price,
    COALESCE(v.sale_price,    p.sale_price)    AS sale_price,
    COALESCE(v.sale_start,    p.sale_start)    AS sale_start,
    COALESCE(v.sale_end,      p.sale_end)      AS sale_end,
    COALESCE(v.weight, p.weight)               AS weight,
    COALESCE(v.length, p.length)               AS length,
    COALESCE(v.width,  p.width)                AS width,
    COALESCE(v.height, p.height)               AS height,
    /* variation images (fallback to product images if none) */
    CASE
      WHEN EXISTS (SELECT 1 FROM variation_image vi WHERE vi.variation_id = v.id)
      THEN (
        SELECT GROUP_CONCAT(s.url, ', ')
        FROM (SELECT DISTINCT vi.url AS url
              FROM variation_image vi
              WHERE vi.variation_id = v.id) AS s
      )
      ELSE (
        SELECT GROUP_CONCAT(s.url, ', ')
        FROM (SELECT DISTINCT pi.url AS url
              FROM product_image pi
              WHERE pi.product_id = p.id) AS s
      )
    END AS Images,
    (SELECT GROUP_CONCAT(va.name || '=' || va.value, ' | ')
     FROM variation_attribute va
     WHERE va.variation_id = v.id)            AS Attributes
  FROM variation v
  JOIN product p ON p.id = v.product_id
)

SELECT *
FROM (
  SELECT Type, SKU, Parent, Name,
         regular_price, sale_price, sale_start, sale_end,
         weight, length, width, height,
         Images, Attributes
  FROM P
  UNION ALL
  SELECT Type, SKU, Parent, Name,
         regular_price, sale_price, sale_start, sale_end,
         weight, length, width, height,
         Images, Attributes
  FROM V
)
ORDER BY
  /* group by the parent SKU prefix (from Parent for variations, else SKU) */
  substr(CASE WHEN Type='variation' THEN Parent ELSE SKU END,
         1, instr(CASE WHEN Type='variation' THEN Parent ELSE SKU END, '-') - 1),
  /* then by the parent SKU numeric part */
  CAST(substr(CASE WHEN Type='variation' THEN Parent ELSE SKU END,
              instr(CASE WHEN Type='variation' THEN Parent ELSE SKU END, '-') + 1) AS INTEGER),
  /* parent rows first, then variations */
  CASE WHEN Type='variation' THEN 1 ELSE 0 END,
  /* finally, by the variation's own numeric suffix (NULL for parents) */
  CAST(substr(SKU, instr(SKU, '-') + 1) AS INTEGER);