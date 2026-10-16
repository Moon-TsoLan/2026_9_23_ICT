import test from 'node:test'
import assert from 'node:assert/strict'
import { addSubject, appendSlice, fitDistance, OperationGate, projectGraph, restoreExplore, saveExplore } from '../web/src/utils/exploreGraph.ts'

const node = (id, kind = 'winner') => ({ id, kind, label: id, weight: 1 })
const base = { nodes: [node('buyer:A', 'buyer'), node('proj:P', 'project')], edges: [{ a: 'proj:P', b: 'buyer:A', role: 'buy', weight: 1 }] }
const slice = (id, offset = 0) => ({ node: node(id), data_version: '1:',
  graph: { nodes: [node(id), node('proj:P', 'project')], edges: [{ a: 'proj:P', b: id, role: 'win', weight: 1 }] },
  pagination: { offset, limit: 1, total: 2, has_more: offset === 0 } })

test('temporary membership never mutates ambient graph and disappears on exit', () => {
  const before = JSON.stringify(base)
  assert.equal(projectGraph(base, [slice('sup:A')]).nodes.length, 3)
  assert.equal(JSON.stringify(base), before)
  assert.deepEqual(projectGraph(base, []), base)
})
test('same identity in search and result renders once, dangling edges are discarded', () => {
  const result = { graph: { nodes: [node('sup:A')], edges: [{ a: 'sup:A', b: 'missing', role: 'win' }] } }
  const g = projectGraph(base, [slice('sup:A')], result)
  assert.equal(g.nodes.filter(n => n.id === 'sup:A').length, 1)
  assert.equal(g.edges.some(e => e.b === 'missing'), false)
})
test('basket stores IDs even when labels collide; commit rechecks capacity and eligibility', () => {
  let basket = []
  for (let i = 0; i < 4; i++) basket = addSubject(basket, { ...node('sup:' + i), label: '同名' })
  assert.equal(new Set(basket.map(n => n.id)).size, 4)
  assert.equal(addSubject(basket, node('sup:0')), basket)
  assert.throws(() => addSubject(basket, node('sup:5')), /最多/)
  assert.throws(() => addSubject([], node('brand:A', 'vendor')), /中标/)
})
test('empty intersection still retains all verified comparison subjects', () => {
  const answer = { graph: { nodes: [], edges: [] } }
  const pinned = [node('sup:A'), node('sup:B')]
  assert.deepEqual(projectGraph(base, [], answer, pinned).nodes.slice(-2).map(n => n.id), pinned.map(n => n.id))
})
test('pagination merges without duplicate edges and rejects another identity/version', () => {
  assert.equal(appendSlice(slice('sup:A'), slice('sup:A', 1)).graph.edges.length, 1)
  assert.throws(() => appendSlice(slice('sup:A'), slice('sup:B')))
  assert.throws(() => appendSlice(slice('sup:A'), { ...slice('sup:A'), data_version: '2:' }))
})
test('late responses after new target, exit or unmount cannot commit', () => {
  const gate = new OperationGate()
  const a = gate.begin(); const signalA = gate.signal
  const b = gate.begin()
  assert.equal(signalA.aborted, true)
  assert.equal(gate.current(a), false)
  assert.equal(gate.current(b), true)
  gate.cancel()
  assert.equal(gate.current(b), false)
})
test('route snapshot preserves basket, camera, breathing phase and base independently', () => {
  const snapshot = { entryIntent: '', overview: { graph: base }, subjects: [{ id: 'sup:A', name: 'A' }],
    mode: 'picking', star: { camera: { position: [1,2,3], target: [0,0,0] }, time: 17 }, slices: [slice('sup:A')] }
  saveExplore(snapshot)
  const restored = restoreExplore()
  assert.deepEqual(restored, snapshot)
  restored.subjects.length = 0
  assert.equal(restoreExplore().subjects.length, 1)
})
test('framing separated subjects fits the limiting viewport axis without a global distance cap', () => {
  for (const aspect of [0.6, 1, 2]) {
    const distance = fitDistance(100, 42, aspect)
    const halfAngle = Math.min(21 * Math.PI / 180, Math.atan(Math.tan(21 * Math.PI / 180) * aspect))
    assert.ok(distance * Math.sin(halfAngle) > 100)
    assert.ok(distance > 105)
  }
})
