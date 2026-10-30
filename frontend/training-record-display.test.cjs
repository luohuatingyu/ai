const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const css = fs.readFileSync(path.join(__dirname, '../backend/static/app.css'), 'utf8');

test('训练参数只排列实际字段，空位置没有整块边框', () => {
  const grid = /\.training-page \.training-record-grid \{([^}]+)\}/.exec(css)?.[1] || '';
  const params = /\.training-page \.training-record-section:nth-of-type\(2\) \.training-record-grid \{([^}]+)\}/.exec(css)?.[1] || '';
  const field = /\.training-page \.training-record-field \{([^}]+)\}/.exec(css)?.[1] || '';

  assert.match(params, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.doesNotMatch(grid, /border:/);
  assert.match(grid, /background:\s*#fff/);
  assert.match(field, /box-shadow:\s*inset -1px -1px 0 #e2e8f0, inset 1px 1px 0 #e2e8f0/);
});

test('训练记录表头使用高对比度文字', () => {
  const header = /\.training-page \.training-record-table-wrap th \{([^}]+)\}/.exec(css)?.[1] || '';

  assert.match(header, /color:\s*#26364d\s*!important/);
  assert.match(header, /font-weight:\s*700/);
});
