const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const css = fs.readFileSync(path.join(__dirname, '../backend/static/app.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../backend/static/index.html'), 'utf8');

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

test('训练记录表头不使用 sticky，避免 Safari 合成层残影', () => {
  const header = /\.training-page \.training-record-table-wrap th \{([^}]+)\}/.exec(css)?.[1] || '';

  assert.doesNotMatch(header, /position:\s*sticky/);
  assert.match(header, /background:\s*#f5f8fc/);
  assert.match(header, /color:\s*#26364d\s*!important/);
  assert.match(header, /font-weight:\s*700/);
});

test('训练记录表头键名统一显示中文', () => {
  // 未知键名回退显示原键，不因映射遗漏 blank 掉列
  assert.match(html, /const zhKey = key => ZH_KEY_LABELS\[key\] \|\| key;/);
  // 逐轮指标表与吞吐耗时表的表头通过 zhKey 输出中文列名
  assert.match(html, /metricKeys\.map\(key => '<th>' \+ esc\(zhKey\(key\)\)/);
  assert.match(html, /performanceKeys\.map\(key => '<th>' \+ esc\(zhKey\(key\)\)/);
  // 训练参数栅格的 dt 标签同样使用中文键名
  assert.match(html, /Object\.entries\(params\)\.map\(\(\[key, value\]\) => row\(zhKey\(key\)/);
});

test('指标、性能与参数键全部登记中文映射', () => {
  // 截取从 ZH_KEY_LABELS 声明到 row 定义前，覆盖数据键与参数键两个映射对象
  const start = html.indexOf('const ZH_KEY_LABELS');
  const end = html.indexOf('const row =');
  const block = start >= 0 && end > start ? html.slice(start, end) : '';

  assert.ok(block, 'index.html 未提供键名映射定义区');
  const keys = ['epoch', 'train_loss', 'val_loss', 'val_accuracy', 'val_mse', 'val_rmse', 'val_r2',
    'perplexity', 'preference_accuracy', 'scope', 'train_seconds', 'validation_seconds',
    'checkpoint_save_seconds', 'checkpoint_publish_seconds', 'epoch_seconds', 'samples', 'steps',
    'effective_tokens', 'samples_per_second', 'tokens_per_second', 'step_ms', 'epochs', 'batch_size',
    'dataset_id', 'target', 'task', 'lr', 'llm_lr', 'hidden_layers', 'hidden_size', 'test_split',
    'seed', 'require_gpu', 'min_vram_mb', 'device_backend', 'n_clusters', 'time_column', 'lookback',
    'forecast_horizon', 'dataset_source', 'dataset_config', 'dataset_split', 'base_model',
    'reward_model', 'text_column', 'prompt_column', 'completion_column', 'chosen_column',
    'rejected_column', 'max_samples', 'max_seq_len', 'vocab_size', 'model_hidden_size',
    'model_layers', 'model_heads', 'ppo_updates', 'parallel_workers'];
  for (const key of keys) {
    assert.match(block, new RegExp('(^|[^\\w])' + key + ':'), `缺少键映射: ${key}`);
  }
  assert.match(block, /train_seconds:\s*"训练耗时（秒）"/);
});

test('浮点小数性能表与训练用时最多保留 4 位', () => {
  const fn = /const formatNumber4 = ([^;\n]+);/.exec(html)?.[1];

  assert.ok(fn, 'index.html 未提供 formatNumber4');
  assert.match(fn, /10000/);
  const formatNumber4 = eval('(' + fn + ')');

  assert.equal(formatNumber4(0.5344615600042744), 0.5345);
  assert.equal(formatNumber4(0.3333333333333333), 0.3333);
  assert.equal(formatNumber4(12), 12);
  assert.equal(formatNumber4(null), null);
  assert.equal(formatNumber4('0.5'), '0.5');
  // 吞吐与阶段耗时表的数值单元格均经过 formatNumber4
  assert.match(html, /<td>' \+ formatValue\(formatNumber4\(item\[key\]\)\) \+ '<\/td>/);
  // 任务概览中的"训练用时"秒数同样最多 4 位小数
  assert.match(html, /formatNumber4\(duration\) \+ " 秒"/);
});
