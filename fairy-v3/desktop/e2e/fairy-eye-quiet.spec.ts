import { expect, test } from '@playwright/test';

for (const reducedMotion of [false, true]) {
  test(`quiet SVG eye retains its centered round pose (reduced motion: ${reducedMotion})`, async ({page}, testInfo) => {
    // Real SVG/CSS renderer, with only the surrounding application excluded.
    // No mock of the eye geometry, DOM transforms or browser layout engine.
    await page.route('**/quiet-eye-regression', route => route.fulfill({
      contentType: 'text/html',
      body: '<html><body style="margin:0;background:#181818"><div id="eye" style="position:absolute;left:80px;top:60px;width:160px;height:160px"></div></body></html>',
    }));
    await page.goto('/quiet-eye-regression');
    await page.evaluate(async (reducedMotion) => {
      const runtimePath='/src/fairyEye/runtime.ts';
      const stylePath='/src/fairyEye/eye.css';
      await import(stylePath);
      const {mountFairyEye}=await import(runtimePath);
      (window as any).quietEye=mountFairyEye(document.getElementById('eye')!, {
        state:'idle', gaze:{x:1,y:1}, reducedMotion,
      });
    }, reducedMotion);
    try {
      for(let i=0;i<20;i++) {
        await page.evaluate(() => (window as any).quietEye.update({state:'sleeping',gaze:{x:1,y:1}}));
        const geometry=await page.locator('.dsh-fairy-sclera > circle').first().evaluate(circle => {
          const bounds=circle.getBoundingClientRect();
          const host=document.getElementById('eye')!.getBoundingClientRect();
          return {
            x:bounds.x+bounds.width/2-host.x-host.width/2,
            y:bounds.y+bounds.height/2-host.y-host.height/2,
            ratio:bounds.width/bounds.height,
          };
        });
        expect(Math.abs(geometry.x)).toBeLessThan(.1);
        expect(Math.abs(geometry.y)).toBeLessThan(.1);
        expect(geometry.ratio).toBeCloseTo(1, 3);
        await expect(page.locator('.dsh-fairy-eyeball')).toHaveCSS('transform','matrix(1, 0, 0, 1, 0, 0)');
        if(i<19) await page.evaluate(() => (window as any).quietEye.update({state:'idle',gaze:{x:-1,y:-1}}));
      }
      await page.screenshot({path:testInfo.outputPath('quiet-centered-eye.png'),clip:{x:40,y:20,width:240,height:240}});
    } finally {
      await page.evaluate(() => (window as any).quietEye?.dispose());
    }
  });
}
