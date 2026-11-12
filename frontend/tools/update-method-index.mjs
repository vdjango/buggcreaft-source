import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'
import { readdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = fileURLToPath(new URL('../', import.meta.url))
const target = path.resolve(root, '../文档/28-前端函数与组件索引.md')
const rows = ['# 前端函数与组件索引', '', '> 自动生成：在 frontend 执行 npm run methods；CI 使用 npm run methods:check。', '',
  '采用 TypeScript 编译器 AST 与 Vue SFC 解析器，不用正则猜测函数。仅记录实际代码，匿名回调用所在行标识；行为契约见第 08 号。', '',
  '| 文件 | 符号 | 源码签名 |', '| --- | --- | --- |']

async function walk(directory) {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
    const filename = path.join(directory, entry.name)
    if (entry.isDirectory()) await walk(filename)
    else if (/\.(ts|js|mjs|vue)$/.test(entry.name)) await indexFile(filename)
  }
}

async function indexFile(filename) {
  const original = await readFile(filename, 'utf8')
  const relative = path.relative(path.resolve(root, '..'), filename).replaceAll('\\', '/')
  const text = filename.endsWith('.vue') ? (() => {
    const { descriptor } = parse(original)
    rows.push(`| [${relative}](../${relative}) | 组件 | Vue SFC |`)
    return descriptor.scriptSetup?.content ?? descriptor.script?.content ?? ''
  })() : original
  const source = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true,
    /\.(m?js)$/.test(filename) ? ts.ScriptKind.JS : ts.ScriptKind.TS)
  const clean = value => value.replaceAll('|', '&#124;').replaceAll('`', '&#96;').replace(/\s+/g, ' ')
  function visit(node) {
    if ((ts.isFunctionLike(node) && node.body) || ts.isClassDeclaration(node)) {
      const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1
      const name = node.name?.getText(source) ??
        (ts.isVariableDeclaration(node.parent) ? node.parent.name.getText(source) : `callback@${line}`)
      const signature = ts.isClassDeclaration(node) ? 'class' :
        `(${node.parameters.map(parameter => parameter.getText(source)).join(', ')})${node.type ? ': ' + node.type.getText(source) : ''}`
      rows.push(`| [${relative}](../${relative}) | ${clean(name)} | ${clean(signature)} |`)
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
}

await walk(path.join(root, 'src'))
await walk(path.join(root, 'tools'))
await walk(path.join(root, 'platform'))
for(const entry of await readdir(path.resolve(root,'../src/mc_translate/web')))if(entry.endsWith('.js')&&!entry.startsWith('vue-'))await indexFile(path.resolve(root,'../src/mc_translate/web',entry))
await indexFile(path.join(root, 'vite.config.ts'))
const result = rows.join('\n') + '\n'
if (process.argv.includes('--check')) {
  if (await readFile(target, 'utf8').catch(() => '') !== result) throw new Error('Frontend method index outdated; npm run methods')
} else await writeFile(target, result)
