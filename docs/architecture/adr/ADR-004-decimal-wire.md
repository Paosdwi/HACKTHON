# ADR-004: Decimal Wire Representation

## Status

Approved — 2026-08-01

## Context

Master 要求 Decimal-safe analysis，但 JSON examples 對 probability/score 使用 JSON number；binary float 與跨語言 serializer 不能保證精度或 canonical identity。以下 v1 grammar 與範圍均為 **Maintainer Proposed Default**。

## Decision

1. v1 core wire 中所有 decimal 值一律為 JSON string；core schema 不接受 JSON number。
2. 最大 precision 為 38 位 significant decimal digits，最大 scale 為 18 位 fractional digits；不得以 rounding 自動修復超限 input。
3. Canonical grammar 禁止科學記號、前置 `+`、不必要 leading zero、trailing fractional zero 與 `-0`。Canonical normalization 規則：
   - zero 只輸出 `"0"`；
   - integer 不帶 decimal point；
   - 絕對值小於 1 的非零值使用 `0.` prefix；
   - fractional part 移除尾端 zero；
   - negative 只在非零值前使用 `-`。
4. 合法 canonical examples：`"0"`、`"1"`、`"-1"`、`"0.42"`、`"1000.000000000000000001"`。非法 examples：`"01"`、`"1.0"`、`"+1"`、`"1e3"`、`"-0"`、`".5"`。
5. `return` 可為負；price 與 volume 必須非負；score、probability、anomaly、confidence 必須在 closed interval `[0,1]`。
6. 一組 mutually exclusive probabilities 的 decimal sum 與 `1` 的絕對差必須 `<= 0.000001`。
7. Master JSON number 僅由 migration/adapter boundary 的 Decimal parser 接受，解析時不得先經 binary float，之後立刻由 canonical serializer 輸出 string。Core v1 schema、Domain/Application command 不接受 number。

## Alternatives considered

- JSON number everywhere：易受 float rounding、serializer/scientific notation 影響。
- 同時接受 string/number 於 core：產生兩種 canonical form 與模糊 validation。
- 固定 18 位尾零：穩定但違反最小 canonical representation，增加 hash 差異。
- arbitrary precision 無上限：造成跨 provider/DB 與 DoS 風險。
- probability 嚴格等於 1：provider decimal quantization 常產生可接受微差。

## Consequences

Wire precision 與 canonical hashing 可重現，但 client/provider 必須使用 Decimal-aware parser。Scale/precision 超限、非 canonical string 或 domain range 違反會被拒絕。Display formatting 必須與 canonical wire 分離。

## Compatibility impact

Master examples 的 JSON number 可由明確 migration/adapter mapper 讀取，但不得直接進 core。既有 number consumer 需升級為 decimal-string parser；response schema 也改為 string。這是 draft contract 的 breaking clarification，必須在 `1.0.0` 凍結前完成。

## Security impact

precision/scale 上限降低巨大 decimal input 的 CPU/memory abuse；禁用多重 lexical forms 降低 signature/hash bypass。Adapter 必須避免 binary float、NaN、Infinity 與 locale-dependent separators。

## Testing requirements

- grammar golden tests：leading/trailing zeros、`+`、scientific notation、`-0`、decimal point edge cases。
- precision 38/39、scale 18/19 邊界與 no-rounding rejection。
- canonical serializer/parser round-trip property tests，不經 binary float。
- return/price/volume/[0,1] domain boundary tests。
- probability sum tolerance：exact、±0.000001 accept，超出最小 increment reject。
- Master number compatibility mapper tests；core schema number rejection tests。
- cross-language canonical vectors 與 hash stability tests。

## Open Questions

- 官方 CSV 各欄原始 scale preservation 是 provenance/display concern 或 canonical value metadata，需 Live Data ADR 決定。
- 未來 major version 是否允許 tagged integer coefficient/scale representation。
- 各 formula 中間運算 context/rounding mode 需 formula spec 另定。

## Source confidence

Master 的 Decimal-safe obligation 與可直接讀取的 number examples 可確認；v1 decimal string grammar、38/18 precision/scale、domain ranges、sum tolerance 與 compatibility adapter 是 architecture design inference，原為 **Maintainer Proposed Default**，已於 2026-08-01 核准。

Master Spec 存在大量不可可靠判讀的中文片段；只採可直接確認的 FR 編號、英文 schema 名稱、JSON 範例、數值、AWS 元件、檔案路徑與清楚驗收；亂碼單獨推導標 SOURCE_TEXT_UNCERTAIN。
