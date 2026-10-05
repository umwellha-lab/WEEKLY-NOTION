import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const source = fs.readFileSync(new URL('../lib/biweekly-report.ts', import.meta.url), 'utf8');
const js = stripTypeScriptTypes(source);
const { mapPublishedReport, queryPublishedReports } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const id = 'a'.repeat(32);
const row = (checked = true, sid = id) => ({ properties: {
  '학생': { relation: [{id: sid}] }, '게시 여부': {checkbox: checked},
  '보고 기간': {date: {start:'2026-09-14', end:'2026-09-27'}},
  '교재단어 평균': {number: 0}, '학원단어 평균': {number: null},
  '교사 의견': {rich_text:[{plain_text:'확인된 교사 의견'}]},
  '검수 메모': {rich_text:[{plain_text:'INTERNAL_ONLY'}]},
} });
test('publication and exact single-student relation prevent cross-student exposure', () => {
  for (const page of [row(false), row(true,'b'.repeat(32)), {...row(), archived:true}, {...row(), in_trash:true}]) assert.equal(mapPublishedReport(page,id), null);
  const multi = row(); multi.properties['학생'].relation.push({id:'b'.repeat(32)});
  assert.equal(mapPublishedReport(multi,id),null);
  const partial = row(); partial.properties['학생'].has_more = true;
  assert.equal(mapPublishedReport(partial,id),null);
});
test('zero, missing values and teacher comments survive; internal notes are excluded', () => {
  const output = mapPublishedReport(row(),id);
  assert.equal(output.report.metrics['교재단어 평균'],0);
  assert.equal(output.report.metrics['학원단어 평균'],null);
  assert.ok(output.report.records.includes('교사 의견: 확인된 교사 의견'));
  assert.ok(!JSON.stringify(output).includes('INTERNAL_ONLY'));
});
test('all pages are read and response rows are checked again', async () => {
  let calls = 0;
  const output = await queryPublishedReports(id, async body => {
    assert.equal(body.filter.and[0].relation.contains,id);
    assert.equal(body.filter.and[1].checkbox.equals,true);
    calls++;
    return calls === 1 ? {results:[row(false),row(true,'b'.repeat(32))],has_more:true,next_cursor:'next'} : {results:[row()],has_more:false};
  });
  assert.equal(calls,2); assert.equal(output.length,1);
});
test('invalid identities skip API calls; repeated cursors fail closed', async () => {
  assert.deepEqual(await queryPublishedReports('invalid',()=>{throw new Error('must not call')}),[]);
  await assert.rejects(queryPublishedReports(id,async()=>({results:[],has_more:true,next_cursor:'same'})));
});
