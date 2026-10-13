import test from 'node:test';
import assert from 'node:assert/strict';
import {render} from './markdown.js';
test('dollars in link destinations never become math', () => {
  const html = render('See [the docs](http://example.com/pricing?tier=$a$) now.');
  assert.match(html, /href="http:\/\/example.com\/pricing\?tier=\$a\$"/);
  assert.doesNotMatch(html, /katex/);
});
test('prices separated by emphasis remain prose', () => {
  const html = render('costs $5 for *x* and $7 for y');
  assert.match(html, /\$5 for <em>x<\/em> and \$7/);
  assert.doesNotMatch(html, /katex/);
});
test('math, escaped dollars and code keep their separate meaning', () => {
  const html = render('\\$5 `$5` $x^2$\n\n$$\\int x \\le y$$\n\n- $z$ beside `code`');
  assert.match(html, /\$5 <code>\$5<\/code>/);
  assert.match(html, /katex-display/);
  assert.doesNotMatch(html, /katex-error/);
  assert.match(html, /<li>.*katex/s);
});
test('math never consumes raw tags or code delimiters', () => {
  const html = render('$a <em>b</em> c$ and $a `b` c$');
  assert.doesNotMatch(html, /katex/);
  assert.match(html, /<code>b<\/code>/);
});
test('multiline display TeX preserves row breaks and subscripts', () => {
  const html = render('$$\n\\begin{aligned}\nx_1 &= y \\\\\nx_2 &= z\n\\end{aligned}\n$$');
  assert.match(html, /katex-display/);
  assert.doesNotMatch(html, /katex-error/);
});
test('inline formula TeX is passed verbatim and HTML links retain policy', () => {
  const html = render('$x_i < y$ [bad](javascript:alert) <img src="https://example.com/image">');
  assert.match(html, /katex/);
  assert.doesNotMatch(html, /katex-error|<img|href="javascript:/);
});

for (const source of ['$a*b*c$', '$\\text{a *b*}$', '$$a*b*c$$', '$$\\text{a *b*}$$']) {
  test(`valid TeX remains math: ${source}`, () => {
    const html = render(source);
    assert.match(html, /class="katex"/);
    assert.doesNotMatch(html, /katex-error/);
  });
}
test('currency and markup retain structure', () => {
  assert.match(render('costs $5 for *x* and $7 for y'), /\$5 for <em>x<\/em> and \$7/);
  for (const source of ['$a <em>b</em> c$', '$a `b` c$', '$a *`b`* c$', '$a [b](https://example.com) c$']) {
    assert.doesNotMatch(render(source), /class="katex"/);
  }
});
