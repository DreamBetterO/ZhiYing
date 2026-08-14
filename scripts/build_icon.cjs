const fs = require("fs");
const sharp = require("sharp");

const source = fs
  .readFileSync("icon/小电视.svg", "utf8")
  .replaceAll("#9da5d0", "#FFF7E8");
const background = Buffer.from(`
  <svg width="1024" height="1024" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="brand" x1="0" y1="0" x2="1" y2="1">
        <stop stop-color="#D44750"/>
        <stop offset="1" stop-color="#7B1020"/>
      </linearGradient>
    </defs>
    <rect x="32" y="32" width="960" height="960" rx="220" fill="url(#brand)"/>
  </svg>
`);

async function main() {
  const foreground = await sharp(Buffer.from(source), { density: 384 })
    .resize(760, 760, { fit: "contain" })
    .png()
    .toBuffer();
  await sharp({
    create: {
      width: 1024,
      height: 1024,
      channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  })
    .composite([
      { input: background },
      { input: foreground, left: 132, top: 132 },
    ])
    .png()
    .toFile("icon/知影-产品图标.png");
  console.log("PRODUCT_ICON_OK");
}

main();
