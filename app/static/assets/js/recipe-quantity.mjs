// Fixed-point preview matches inventory's six-decimal quantity representation.
export function batchMaterial(quantity, good, unit, original={}) {
  if (!/^\d{1,12}(\.\d{1,6})?$/.test(quantity) || !/^\d+$/.test(good) || BigInt(good)<=0n || !unit) return null;
  if (quantity===original.piece && good===String(original.good) && unit===original.unit) return original.total;
  const [whole, fraction='']=quantity.split('.');
  const total=(BigInt(whole)*1000000n+BigInt(fraction.padEnd(6,'0')))*BigInt(good);
  if(total<=0n || total>100000000000000n) return null;
  return `${total/1000000n}.${String(total%1000000n).padStart(6,'0')}`.replace(/\.?0+$/,'');
}
