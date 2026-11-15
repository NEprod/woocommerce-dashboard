/** Shared by the Shopify Function and local quote preview. All money is pence. */
export const MAX_UNITS = 48;
export const MAX_LINES = 24;

export function cents(value) {
  const match = /^([0-9]{1,6})(?:\.([0-9]{1,2})0*)?$/.exec(String(value));
  return match ? Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0')) : null;
}

export function exactQuote(lines, offer) {
  if (!Array.isArray(lines) || lines.length > MAX_LINES ||
      !offer || typeof offer.repeat !== 'boolean' || !Array.isArray(offer.variants) ||
      !offer.variants.length || offer.variants.length > 100 ||
      offer.variants.some(v => typeof v !== 'string' || !/^gid:\/\/shopify\/ProductVariant\/[0-9]{1,30}$/.test(v)) ||
      !Array.isArray(offer.tiers) || !offer.tiers.length || offer.tiers.length > 8 ||
      offer.tiers.some(t => !t || !Number.isInteger(t.qty) || t.qty < 2 || t.qty > 24 ||
        !Number.isInteger(t.total) || t.total < 1 || t.total > 1000000) ||
      new Set(offer.tiers.map(t => t.qty)).size !== offer.tiers.length) return null;
  const tiers = [...offer.tiers].sort((a,b) => b.qty - a.qty);
  const eligible = new Set(offer.variants);
  const seen = new Set();
  const units = [];
  for (const line of lines) {
    if (!line || !eligible.has(line.variant)) continue;
    if (!Number.isInteger(line.quantity) || line.quantity < 1 || typeof line.id !== 'string' || !line.id ||
        seen.has(line.id) || !Number.isInteger(line.pence) || line.pence < 0 || line.pence > 1000000) return null;
    seen.add(line.id);
    if (units.length + line.quantity > MAX_UNITS) return null;
    for (let i = 0; i < line.quantity; i++) units.push(line);
  }
  units.sort((a,b) => b.pence - a.pence || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const prefix = [0];
  for (const unit of units) prefix.push(prefix[prefix.length - 1] + unit.pence);
  // For any given quantity the most expensive eligible units give the greatest
  // savings. Partition that prefix into profitable sets using bounded DP.
  const score = new Array(units.length + 1).fill(-1);
  const chosen = new Array(units.length + 1).fill(null);
  score[0] = 0;
  let best = 0;
  for (let end = 1; end <= units.length; end++) {
    for (const tier of tiers) {
      const start = end - tier.qty;
      if (start < 0 || score[start] < 0 || (!offer.repeat && start !== 0)) continue;
      const savings = prefix[end] - prefix[start] - tier.total;
      if (savings > 0 && score[start] + savings > score[end]) {
        score[end] = score[start] + savings;
        chosen[end] = {start, savings};
      }
    }
    // Equal benefits use fewer units; equal plans use the fixed tier order.
    if (score[end] > score[best]) best = end;
  }
  const allocations = new Map();
  let groups = 0;
  for (let end = best; end > 0;) {
    const {start, savings} = chosen[end];
    const total = prefix[end] - prefix[start];
    const shares = [];
    let remaining = savings;
    // Proportional allocation, then largest remainder. All intermediate
    // arithmetic is below Number.MAX_SAFE_INTEGER at the enforced bounds.
    for (let i = start; i < end; i++) {
      const unit = units[i];
      const product = unit.pence * savings;
      const remainder = product % total;
      const amount = (product - remainder) / total;
      shares.push({unit, amount, remainder, index:i});
      remaining -= amount;
    }
    shares.sort((a,b) => b.remainder - a.remainder || a.index - b.index);
    for (const share of shares) {
      const summary = allocations.get(share.unit.id) || {id:share.unit.id, quantity:0, pence:0};
      summary.quantity++;
      summary.pence += share.amount + (remaining-- > 0 ? 1 : 0);
      allocations.set(summary.id,summary);
    }
    groups++;
    end = start;
  }
  return {savings:score[best], groups, allocations:[...allocations.values()].filter(a => a.pence > 0)};
}
