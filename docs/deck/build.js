const pptxgen = require('pptxgenjs');
const html2pptx = require('./html2pptx');
const fs = require('fs');

(async () => {
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_16x9';
  pptx.title = 'Ramco Payce support triage pilot';
  pptx.author = 'Shivam Dixit';

  const files = fs.readdirSync('slides').filter(f => f.endsWith('.html')).sort();
  for (const f of files) {
    process.stdout.write(`  ${f} ... `);
    await html2pptx(`slides/${f}`, pptx);
    console.log('ok');
  }
  await pptx.writeFile({ fileName: 'ramco-payce-pilot.pptx' });
  console.log(`\nwrote ramco-payce-pilot.pptx (${files.length} slides)`);
})().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
