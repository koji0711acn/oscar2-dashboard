# QAプロファイル: ブログ記事

## 定量チェック合格条件
- overall score >= 6
- incorrect_count = 0
- misleading_serious < 2
- misleading_total < 5
- nested_p <= 5
- broken_summary < 6
- コスト <= $0.60

## 構造チェック
- visual-card = 0
- 意図しないblockquote = 0（SNS embed以外）
- 意図しないfigure/iframe = 0（SNS embed以外）
- サムネイル存在（フォールバックでないこと）
- H1 = 1, H2 >= 3

## 事実チェック
- incorrect = 0
- 日付ハルシネーションなし
- unverifiable項目にヘッジ表現あり

## SNSチェック（gossip_marriageテンプレート）
- adopted_count >= 2（理想3以上）
- 採用投稿のtopic_relevance >= 0.5
- 採用投稿が記事テーマに直接関連していること（テーマ無関係の投稿は不可）
- HTMLにSNS embedセクション存在
- profileテンプレートの場合はSNS不要
- embed=0の場合: 画像フォールバックが正常に挿入されていること

## 定性チェック
- リード文で結論明示
- 同一事実の3回以上の繰り返しなし
- 定型句の3回以上の頻出なし
- 見出しと本文の対応
- 曖昧なソース参照が2件以上ない
