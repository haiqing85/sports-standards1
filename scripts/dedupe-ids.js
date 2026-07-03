/**
 * scripts/dedupe-ids.js
 *
 * 自动检测并修复 data/standards.json 中的系统ID冲突。
 * 在 GitHub Actions 中运行，于 generate-static-pages.js 之前执行，
 * 确保任何来源（后台手动录入、CNB爬虫直接写入等）造成的ID冲突，
 * 都会在生成静态页面之前被自动发现并修复，不依赖人工肉眼排查。
 *
 * ⚠️ 安全原则：
 *   1. 绝不修改"标准编号"（code）字段本身——保持官方原文不变。
 *   2. 只修改内部系统ID（id 字段），重复的ID自动加字母后缀区分。
 *   3. 若被改名的记录原本关联着PDF文件（localFile），不做自动的
 *      GitHub文件搬迁（风险太高，可能张冠李戴）。而是清空该记录的
 *      localFile，标记为"缺PDF"，交由人工核实后手动重新上传——
 *      这样即使自动修复有偏差，也只会表现为"明显可见的缺PDF提示"，
 *      而不是"静默的错误PDF关联"，属于更安全的失败模式。
 */

import fs from 'fs';
import path from 'path';

const DATA_PATH = path.join(process.cwd(), 'data', 'standards.json');

function main() {
  if (!fs.existsSync(DATA_PATH)) {
    console.log('📭 未找到 data/standards.json，跳过ID去重检查');
    return;
  }

  const raw = fs.readFileSync(DATA_PATH, 'utf-8');
  const data = JSON.parse(raw);
  const stds = data.standards || [];

  if (!stds.length) {
    console.log('📭 standards.json 中没有数据，跳过ID去重检查');
    return;
  }

  // 第一遍扫描：统计每个id出现的次数，同时收集所有已被占用的id（用于后续生成新id时避免二次冲突）
  const usedIds = new Set();
  const idCounts = new Map();
  for (const s of stds) {
    if (!s.id) continue;
    idCounts.set(s.id, (idCounts.get(s.id) || 0) + 1);
    usedIds.add(s.id);
  }

  const duplicateIds = [...idCounts.entries()].filter(([, count]) => count > 1).map(([id]) => id);

  if (!duplicateIds.length) {
    console.log('✅ 未发现ID冲突，' + stds.length + ' 条标准的系统ID全部唯一');
    return;
  }

  console.log('⚠️ 发现 ' + duplicateIds.length + ' 组ID冲突，开始自动修复…');

  // 为每个冲突id分配后缀：第1次出现保持原样，第2次起依次加 B、C、D...
  // 若加到 Z 还不够（现实中几乎不可能），继续用 B2、B3... 兜底，确保绝不会生成重复id
  const seenCount = new Map();
  let fixedCount = 0;
  const fixLog = [];

  function nextSuffix(n) {
    // n=2 → 'B', n=3 → 'C', ..., n=27 → 'Z2', n=28 → 'Z3' ...（极端兜底，正常情况用不到）
    if (n <= 26) return String.fromCharCode(64 + n); // 2->B ... 26->Z
    return 'Z' + (n - 25);
  }

  for (const s of stds) {
    if (!s.id) continue;
    const originalId = s.id;
    const occurrence = (seenCount.get(originalId) || 0) + 1;
    seenCount.set(originalId, occurrence);

    if (occurrence === 1) continue; // 第一次出现，保留原id不动

    // 第2次及以后出现：生成一个不冲突的新id
    let newId = originalId + nextSuffix(occurrence);
    let guard = 0;
    while (usedIds.has(newId) && guard < 50) {
      guard++;
      newId = originalId + nextSuffix(occurrence) + guard;
    }
    usedIds.add(newId);

    var hadPdf = !!s.localFile;
    var oldLocalFile = s.localFile;
    s.id = newId;
    if (hadPdf) {
      // 不做自动PDF搬迁，清空关联并标记，避免可能的张冠李戴
      s.localFile = null;
    }

    fixedCount++;
    fixLog.push(
      '  · ' + (s.code || '未知编号') + '「' + (s.title || '') + '」：' +
      originalId + ' → ' + newId +
      (hadPdf ? '（原关联PDF「' + oldLocalFile + '」已清空，请人工核实后重新上传）' : '')
    );
  }

  fs.writeFileSync(DATA_PATH, JSON.stringify(data, null, 2), 'utf-8');

  console.log('✅ 已自动修复 ' + fixedCount + ' 条ID冲突：');
  fixLog.forEach(line => console.log(line));
  console.log('📝 data/standards.json 已更新，将随本次提交一并推送');
}

main();
