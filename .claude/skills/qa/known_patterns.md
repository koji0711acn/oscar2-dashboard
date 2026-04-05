# Known Patterns（既知の失敗パターン）

## [2026-03] visual-card/blockquote再注入
- 症状: パイプラインで除去したvisual-cardが最終HTMLに復活
- 原因: app.pyの_add_visuals_to_article()が再注入
- 修正済み: commit 5c410e5
- 教訓: ローカルとRailwayでコードパスが異なる

## [2026-03] pics/がgitに含まれていない
- 症状: サムネイルが常にフォールバック
- 修正済み: commit 32aa760

## [2026-04-03] 日付ハルシネーション
- 症状: 記事生成日がソース日付として使われる
- 修正済み: commit af17610（4層防御）

## [2026-04-03] SNSクエリに核心語不足
- 症状: クエリに相手名が入らず無関係投稿ばかり収集
- 修正済み: commit 9bf0401 + 後続修正

## [2026-04-03] prefilterのnoise_pattern誤reject
- 症状: テーマ関連投稿がnoise_patternで弾かれる
- 修正済み: commit 9bf0401

## [2026-04-04] SNS selectorがテーマ無関係投稿を通過させる
- 症状: 中森明菜の結婚記事に「ライブツアー決定」の投稿がadopted
- 原因: topic_relevance=0.6で通過。テーマとの直接関連を判定していない
- 修正: 調査中
- 教訓: topic_relevanceの数値だけでなく、記事キーワードとの直接的関連を確認すべき
