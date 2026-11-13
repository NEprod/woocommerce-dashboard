// Bounded container checks, not a decoder or malware scanner. Accept only the
// three raster formats; bound decoded dimensions and reject animated WebP/PNG.
const ascii = (b, i, s) => [...s].every((c,j)=>b[i+j]===c.charCodeAt(0));
const pixels = (w,h) => w>0 && h>0 && w*h<=40_000_000;
const crcTable=Uint32Array.from({length:256},(_,n)=>{
  for(let k=0;k<8;k++)n=(n>>>1)^((n&1)?0xedb88320:0);
  return n;
});
function crc(b,start,end) {
  let value=0xffffffff;
  for(let i=start;i<end;i++)value=(value>>>8)^crcTable[(value^b[i])&255];
  return (value^0xffffffff)>>>0;
}

export function imageType(b) {
  if (b.length < 20) return null;
  const v = new DataView(b.buffer,b.byteOffset,b.byteLength);
  if (b[0]===255 && b[1]===216 && b.at(-2)===255 && b.at(-1)===217) {
    let size=false;
    for(let i=2;i+4<b.length;) {
      if(b[i++]!==255) return null;
      while(b[i]===255)i++;
      if(i+3>=b.length)return null;
      const marker=b[i++], n=v.getUint16(i);
      if(n<2||i+n>b.length-2)return null;
      if([0xc0,0xc1,0xc2].includes(marker)) {
        if(n<8||!pixels(v.getUint16(i+5),v.getUint16(i+3)))return null;
        size=true;
      }
      if(marker===0xda)return size && n>=6 && i+n<b.length-2 ? 'image/jpeg' : null;
      i+=n;
    }
  }
  if (v.getUint32(0)===0x89504e47 && v.getUint32(4)===0x0d0a1a0a) {
    let data=false, chunks=0;
    for(let i=8;i+12<=b.length;) {
      const n=v.getUint32(i), end=i+12+n;
      if(end>b.length || ++chunks>1024 || crc(b,i+4,end-4)!==v.getUint32(end-4))return null;
      if(i===8 && (n!==13||!ascii(b,i+4,'IHDR')||!pixels(v.getUint32(i+8),v.getUint32(i+12))))return null;
      if(ascii(b,i+4,'acTL'))return null;
      if(ascii(b,i+4,'IDAT')&&n)data=true;
      if(ascii(b,i+4,'IEND'))return data && !n && end===b.length ? 'image/png' : null;
      i=end;
    }
  }
  if (ascii(b,0,'RIFF') && ascii(b,8,'WEBP') && v.getUint32(4,true)===b.length-8) {
    let frame=false;
    for(let i=12;i<b.length;) {
      if(i+8>b.length)return null;
      const n=v.getUint32(i+4,true), d=i+8, end=d+n+(n%2);
      if(end>b.length)return null;
      if(ascii(b,i,'VP8X') && (n!==10 || b[d]&2 || !pixels(1+(v.getUint32(d+4,true)&0xffffff),1+(v.getUint32(d+6,true)>>>8))))return null;
      if(ascii(b,i,'ANIM')||ascii(b,i,'ANMF'))return null;
      if(ascii(b,i,'VP8 ')){
        if(frame||n<11||b[d]&1||b[d+3]!==157||b[d+4]!==1||b[d+5]!==42
            ||!pixels(v.getUint16(d+6,true)&0x3fff,v.getUint16(d+8,true)&0x3fff))return null;
        frame=true;
      }
      if(ascii(b,i,'VP8L')){
        if(frame||n<6||b[d]!==47)return null;
        const dims=v.getUint32(d+1,true);
        if(dims>>>29||!pixels((dims&0x3fff)+1,((dims>>>14)&0x3fff)+1))return null;
        frame=true;
      }
      i=end;
    }
    if(frame)return 'image/webp';
  }
  return null;
}
