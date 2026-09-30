<script setup lang="ts">
import type { CobRecord } from '@/types/search'
import { fmtQty, fmtYuan, orDash } from '@/utils/format'

defineProps<{ record: CobRecord | null; loading?: boolean }>()

const CATEGORY_TYPE_LABEL: Record<string, string> = { A: '货物', B: '工程', C: '服务' }
</script>

<template>
  <div v-if="loading" class="pad">
    <div class="skeleton h-5 w-2/3" />
    <div class="skeleton mt-4 h-4 w-full" />
    <div class="skeleton mt-2 h-4 w-5/6" />
    <div class="skeleton mt-6 h-4 w-full" />
    <div class="skeleton mt-2 h-4 w-4/6" />
  </div>

  <div v-else-if="record" class="pad">
    <p class="kicker">标的物全量信息</p>
    <h2 class="h-serif mt-2 text-[20px] leading-snug">{{ record.object_name }}</h2>
    <p class="mt-1 text-[12px] text-muted">{{ record.project_name }} · 包{{ record.package_no }}</p>

    <section>
      <h3>标的字段</h3>
      <dl>
        <div><dt>品目</dt><dd>{{ orDash(record.category_name) }}<span v-if="record.category_type" class="text-muted">（{{ CATEGORY_TYPE_LABEL[record.category_type] ?? record.category_type }}）</span></dd></div>
        <div><dt>品目编码</dt><dd class="num">{{ orDash(record.category_code) }}</dd></div>
        <div><dt>品牌</dt><dd>{{ orDash(record.brand) }}</dd></div>
        <div><dt>产品供应商</dt><dd>{{ orDash(record.product_supplier) }}</dd></div>
        <div><dt>规格型号</dt><dd>{{ orDash(record.spec_model) }}</dd></div>
        <div><dt>单价</dt><dd class="num">{{ fmtYuan(record.unit_price) }}</dd></div>
        <div><dt>数量</dt><dd class="num">{{ fmtQty(record.quantity) }} {{ record.unit ?? '' }}</dd></div>
        <div><dt>总价</dt><dd class="num font-semibold">{{ fmtYuan(record.total_price) }}</dd></div>
      </dl>
    </section>

    <section>
      <h3>项目与采购</h3>
      <dl>
        <div>
          <dt>采购单位</dt>
          <dd>
            <RouterLink v-if="record.purchaser" class="link" :to="`/party/${encodeURIComponent('buyer:' + record.purchaser)}`">
              {{ record.purchaser }}
            </RouterLink>
            <template v-else>—</template>
          </dd>
        </div>
        <div><dt>包金额</dt><dd class="num">{{ fmtYuan(record.package_total_amount) }}</dd></div>
      </dl>
    </section>

    <section>
      <h3>中标与投标</h3>
      <dl>
        <div>
          <dt>中标供应商</dt>
          <dd>
            <RouterLink v-if="record.winner" class="link" :to="`/party/${encodeURIComponent('sup:' + record.winner.supplier_name)}`">
              {{ record.winner.supplier_name }}
            </RouterLink>
            <template v-else>—</template>
          </dd>
        </div>
        <div v-if="record.winner?.score != null"><dt>中标得分</dt><dd class="num">{{ record.winner.score }}</dd></div>
      </dl>
      <ul v-if="record.bidders?.length" class="bidders">
        <li v-for="b in record.bidders" :key="b.supplier_name" :class="{ win: b.is_winner }">
          <span class="truncate">{{ b.supplier_name }}</span>
          <em v-if="b.is_winner">中标</em>
          <span v-else class="num text-muted">{{ b.score ?? '—' }}</span>
        </li>
      </ul>
    </section>

    <section>
      <h3>溯源</h3>
      <dl>
        <div><dt>来源公告</dt><dd class="num text-[12px]">{{ record.announcement_id }}</dd></div>
        <div><dt>来源类型</dt><dd>{{ orDash(record.provenance.source_type?.toUpperCase()) }}</dd></div>
      </dl>
    </section>
  </div>
</template>

<style scoped>
.pad {
  padding: 18px 20px 24px;
}

section {
  margin-top: 18px;
}

h3 {
  border-bottom: 1px solid var(--line);
  padding-bottom: 6px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.14em;
}

dl {
  margin-top: 8px;
}

dl > div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 5px 0;
  font-size: 13px;
}

dt {
  flex-shrink: 0;
  color: var(--muted);
}

dd {
  min-width: 0;
  text-align: right;
  word-break: break-all;
}

.link {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.bidders {
  margin-top: 8px;
  border-top: 1px dashed var(--line);
  padding-top: 6px;
}

.bidders li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  font-size: 12px;
}

.bidders li.win {
  font-weight: 500;
}

.bidders em {
  flex-shrink: 0;
  border-radius: var(--r-sm);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  padding: 0 6px;
  color: var(--accent);
  font-size: 11px;
  font-style: normal;
}
</style>
