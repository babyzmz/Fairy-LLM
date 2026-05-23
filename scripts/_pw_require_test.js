const path = require('path');
console.log('node ok');
(async()=>{
  try {
    const mod = require('playwright');
    console.log('require playwright ok', Object.keys(mod).slice(0,5));
  } catch (err) {
    console.error('require playwright failed', err && err.message);
    process.exitCode = 2;
  }
})();
