# QA Report: test_qa_bad_article.html

**Verdict: FAIL**
**Type:** article
**Title:** 鬼束ちひろ 結婚 旦那 現在の姿に驚き
**Keywords:** 鬼束ちひろ, 結婚
**Issues:** 11 (critical: 4, warning: 7)

## Issues Found

### 1. [CRITICAL] irrelevant_sns

SNS embed #1 is unrelated to keywords ['鬼束ちひろ', '結婚']

> 今日のサッカー日本代表の試合、最高だった！三笘薫のドリブルが神すぎる。ワールドカップが楽しみ！ #サッカー日本代表 #三笘薫 — サッカーファン太郎 (@soccer_fan_taro) March 

### 2. [CRITICAL] irrelevant_sns

SNS embed #2 is unrelated to keywords ['鬼束ちひろ', '結婚']

> 新しいiPhone16買った！カメラの性能がすごすぎて感動。特にナイトモードが進化してる。 #iPhone16 #Apple — ガジェット好き (@gadget_lover) September 2

### 3. [CRITICAL] text_duplication

Sentence repeated 5 times

> 彼女の独特な世界観は多くの人々を魅了し続けています

### 4. [WARNING] html_error

Mismatched HTML tag: Expected </h2> but found </h3>

### 5. [WARNING] html_error

Mismatched HTML tag: Expected </strong> but found </p>

### 6. [WARNING] html_error

Mismatched HTML tag: Expected </span> but found </article>

### 7. [CRITICAL] opening_irrelevant

Opening body text (300 chars) does not address: ['鬼束ちひろ', '結婚']. The article should answer the reader's query immediately.

> 最近、日本の音楽シーンではさまざまなジャンルのアーティストが活躍しています。ストリーミングサービスの普及により、音楽の楽しみ方も大きく変わってきました。サブスクリプションモデルの台頭により、CDの売上は減少傾向にありますが、ライブやコンサートの需要は依然として高い状態が続いています。 2024年の音

### 8. [WARNING] missing_img_alt

Image #1 missing alt attribute: src="onitsuka.jpg"

> onitsuka.jpg

### 9. [WARNING] placeholder_link

Placeholder link found: href="#" text="詳しくはこちら"

> 詳しくはこちら

### 10. [WARNING] placeholder_link

Placeholder link found: href="#" text="公式サイト"

> 公式サイト

### 11. [WARNING] missing_meta

Missing meta description tag
