# シナリオ制御設計書

## 1. 全体アーキテクチャ概要

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Client Device                                │
│  ┌────────────┐    ┌──────────────────────────────────────────────┐ │
│  │  Microphone │───▶│           AudioProcessor                    │ │
│  │  (PyAudio)  │    │  PCM16 24kHz mono / 50ms chunks / base64   │ │
│  │  Speaker    │◀───│  Capture → WebSocket │ WebSocket → Playback │ │
│  └────────────┘    └──────────┬───────────┴──────────▲───────────┘ │
└───────────────────────────────┼──────────────────────┼─────────────┘
                                │ WebSocket (duplex)   │
┌───────────────────────────────▼──────────────────────┴─────────────┐
│                   Azure Voice Live API                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  VoiceLiveConnection (azure.ai.voicelive.aio)               │   │
│  │                                                              │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐  │   │
│  │  │  STT     │  │  LLM (GPT)   │  │  TTS (Azure Speech)   │  │   │
│  │  │ (azure-  │  │  gpt-realtime│  │  ja-JP-Masaru:Dragon  │  │   │
│  │  │  speech) │  │              │  │  HDLatestNeural        │  │   │
│  │  └────┬─────┘  └──┬───────┬──┘  └───────────▲───────────┘  │   │
│  │       │ transcript │      │ function_call    │ text          │   │
│  │       ▼           ▼      ▼                  │               │   │
│  │  ┌─────────────────────────────┐             │               │   │
│  │  │  Server Event Stream        │─────────────┘               │   │
│  │  │  (ServerEventType.*)        │                             │   │
│  │  └─────────────┬───────────────┘                             │   │
│  └────────────────┼────────────────────────────────────────────┘   │
└───────────────────┼────────────────────────────────────────────────┘
                    │ events (JSON)
┌───────────────────▼────────────────────────────────────────────────┐
│                  ScenarioClient (voice-live-scenario.py)            │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  classify_and_act (FunctionTool)                            │   │
│  │  LLM が毎ターン呼び出す → intent / slots / user_summary    │   │
│  └────────────────────────┬────────────────────────────────────┘   │
│                           │                                         │
│  ┌────────────────────────▼────────────────────────────────────┐   │
│  │               ScenarioEngine (State Machine)                │   │
│  │  ┌──────────────┐  ┌─────────────┐  ┌──────────────────┐   │   │
│  │  │ Slot Store   │  │ Transition  │  │ Safety Evaluator │   │   │
│  │  │ (dict)       │  │ Evaluator   │  │ (regex rules)    │   │   │
│  │  └──────────────┘  └─────────────┘  └──────────────────┘   │   │
│  └────────────────────────┬────────────────────────────────────┘   │
│                           │ EngineResult                            │
│                           ▼                                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  response_instructions → FunctionCallOutputItem              │   │
│  │  → ResponseCreateParams(tool_choice="none") → LLM 音声応答  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. コンポーネント詳細

### 2.1 YAML シナリオ定義 (`scenario/scenarios/*.yaml`)

シナリオの全構成を宣言的に記述する YAML ファイル。コード変更不要で会話フローを追加・変更可能。

```yaml
# トップレベル構造
name: string              # シナリオ識別名
initial_state: string     # 初期状態名
global_instructions: str  # LLM へのシステムプロンプト（全状態共通）
slots: {}                 # スロット定義
intents: {}               # インテント定義
states: {}                # 状態定義（State Machine のノード）
safety_rules: []          # 安全ルール定義
```

#### スロット定義

| フィールド    | 型          | 説明                              |
|-------------|------------|----------------------------------|
| `type`      | `str\|enum\|int` | スロットの型                  |
| `required`  | `bool`     | 必須かどうか                       |
| `values`    | `list[str]`| enum 型の場合の許可値リスト          |
| `default`   | `str\|null`| デフォルト値                       |
| `description`| `str`     | 説明（LLM に渡す JSON Schema に利用）|

#### インテント定義

| フィールド            | 型          | 説明                          |
|---------------------|------------|------------------------------|
| `description`       | `str`      | インテントの説明                 |
| `example_utterances`| `list[str]`| 発話例（LLM の分類精度向上用）     |

#### 状態定義

| フィールド          | 型              | 説明                                    |
|-------------------|----------------|----------------------------------------|
| `instructions`    | `str`          | その状態での LLM 応答指示（テンプレート変数対応） |
| `intents`         | `list[str]`    | その状態で認識するインテント一覧              |
| `transitions`     | `list[Transition]` | 遷移ルール（後述）                     |
| `required_slots`  | `list[str]`    | その状態で必要なスロット                     |
| `max_turns`       | `int\|null`    | 最大ターン数                              |
| `is_terminal`     | `bool`         | 終端状態かどうか                           |

#### 遷移ルール (Transition)

| フィールド          | 型          | 説明                                          |
|-------------------|------------|----------------------------------------------|
| `target_state`    | `str`      | 遷移先の状態名                                  |
| `intent`          | `str\|null`| マッチするインテント（省略時は任意）                  |
| `condition`       | `str\|null`| 追加条件式 (例: `all_required_slots_filled`, `question_count >= 5`) |
| `required_slots`  | `list[str]`| 遷移に必要なスロット                              |
| `actions`         | `list[str]`| 遷移時の副作用アクション                           |

---

### 2.2 データモデル (`scenario/models.py`)

```
ScenarioConfig          # YAML から生成される全体設定
├── SlotDefinition      # スロット1つの定義
├── IntentDefinition    # インテント1つの定義
├── StateConfig         # 状態1つの設定
│   └── Transition      # 遷移ルール
├── SafetyRule          # 安全ルール1つ
└── EngineResult        # エンジン処理結果（毎ターン返却）
```

**EngineResult** の内容:

| フィールド               | 型              | 説明                          |
|------------------------|----------------|------------------------------|
| `state_name`           | `str`          | 現在の状態名                    |
| `response_instructions`| `str`          | LLM への応答指示テキスト          |
| `collected_slots`      | `dict[str,str]`| 収集済みスロット                  |
| `missing_slots`        | `list[str]`    | 未収集の必須スロット               |
| `is_terminal`          | `bool`         | 終端状態到達かどうか              |
| `escalation`           | `bool`         | エスカレーション発動か             |
| `actions`              | `list[str]`    | 実行されたアクション               |

---

### 2.3 YAML ローダー (`scenario/loader.py`)

```
YAML ファイル
    │
    ▼
load_scenario(path)
    │
    ├── yaml.safe_load() で読み込み
    ├── slots / intents / states / safety_rules をパース
    ├── _validate() でバリデーション
    │   ├── initial_state が states に存在するか
    │   ├── 全 transition の target_state が存在するか
    │   ├── 全 required_slots が slots に定義されているか
    │   └── 全 intents が intents に定義されているか
    │
    └── ScenarioConfig を返却
```

---

### 2.4 ScenarioEngine（状態機械コア）(`scenario/engine.py`)

#### 初期化

```python
ScenarioEngine(config: ScenarioConfig)
    ├── current_state = config.initial_state
    ├── slot_store = {} (+ defaults)
    ├── turn_count = 0
    ├── history = []
    └── _safety = SafetyEvaluator(config.safety_rules)
```

#### 毎ターンの処理フロー (`process()`)

```
process(intent, slots, user_summary)
    │
    ├── 1. turn_count++
    │
    ├── 2. _update_slots(slots)
    │      └── enum バリデーション、未定義スロット警告
    │
    ├── 3. history に記録
    │
    ├── 4. _safety.evaluate()  ← SafetyEvaluator
    │      ├── escalation → 即座に escalation 状態へ遷移
    │      ├── prohibited (block) → 現状態維持 + 安全メッセージ
    │      └── guardrail (warn) → 現状態維持 + 警告メッセージ
    │
    ├── 5. _find_transition()
    │      ├── intent マッチ
    │      ├── condition 評価
    │      │   ├── "all_required_slots_filled" → 必須スロット充足チェック
    │      │   └── "slot >= N" / "slot > N" → 数値比較
    │      ├── required_slots チェック
    │      └── 最初にマッチした遷移を採用
    │
    ├── 6. _execute_actions()
    │      ├── reset_question_count → slot_store["question_count"] = "0"
    │      ├── increment_question_count → +1
    │      └── clear_slots → 全スロットクリア（デフォルト値は復元）
    │
    ├── 7. 遷移先状態の instructions を取得
    │      └── {slot_name} テンプレート変数を slot_store で展開
    │
    ├── 8. 未収集スロットがあれば instructions に追記
    │
    └── 9. EngineResult を返却
```

#### 条件評価のサポート

| 条件式                       | 評価方法                              |
|-----------------------------|--------------------------------------|
| `all_required_slots_filled` | 現状態の required_slots が全て slot_store に存在するか |
| `slot_name >= N`            | slot_store[slot_name] を int 変換して比較      |
| `slot_name > N`             | 同上（より大きい）                         |

---

### 2.5 SafetyEvaluator（安全ガードレール）(`scenario/safety.py`)

```
SafetyEvaluator(rules: list[SafetyRule])
    │
    └── evaluate(user_summary, intent, slots) → SafetyResult
         │
         ├── 入力テキスト = intent + user_summary + slot values を結合
         │
         └── 優先順位順に正規表現マッチ:
              1. escalation (最優先) → action=escalate → 有人引継ぎ
              2. prohibited          → action=block    → 話題拒否
              3. guardrail           → action=warn     → 軽い警告
```

**SafetyResult:**
- `triggered: bool` — ルール発動したか
- `action: str` — `"block"` / `"escalate"` / `"warn"`
- `message: str` — 応答指示に含めるメッセージ
- `rule_type: str` — 発動したルールの種別

---

## 3. Azure Voice Live API 連携

### 3.1 接続とセッション設定

```
ScenarioClient.start()
    │
    ├── connect(endpoint, credential, model) → VoiceLiveConnection
    │   ├── WebSocket 接続確立
    │   └── 認証: AzureKeyCredential or AzureCliCredential (Entra ID)
    │
    └── _setup_session()
         │
         ├── RequestSession 構成:
         │   ├── modalities: [TEXT, AUDIO]
         │   ├── instructions: global_instructions + 重要ルール
         │   ├── voice: AzureStandardVoice (ja-JP, temperature, rate, lexicon)
         │   ├── input_audio_format: PCM16
         │   ├── output_audio_format: PCM16
         │   ├── turn_detection: AzureSemanticVadMultilingual
         │   │   ├── threshold: 0.5
         │   │   ├── prefix_padding_ms: 300
         │   │   ├── silence_duration_ms: 700
         │   │   ├── languages: ["ja"]
         │   │   └── remove_filler_words: true
         │   ├── input_audio_echo_cancellation: AudioEchoCancellation
         │   ├── input_audio_noise_reduction: azure_deep_noise_suppression
         │   ├── tools: [classify_and_act]
         │   ├── tool_choice: REQUIRED  ← 毎ターン必ず関数呼び出し
         │   ├── interim_response: StaticInterimResponseConfig
         │   │   ├── triggers: [TOOL, LATENCY]
         │   │   ├── latency_threshold_ms: 500
         │   │   └── texts: ["少々お待ちください。", "確認しております。"]
         │   └── input_audio_transcription:
         │       ├── model: "azure-speech"
         │       ├── language: "ja"
         │       └── phrase_list: [カスタム語彙リスト]
         │
         └── conn.session.update(session=session_config)
```

### 3.2 Server Event 処理フロー

```
_process_events() — async for event in conn:
    │
    ├── SESSION_UPDATED
    │   ├── session_ready = True
    │   ├── _send_greeting() → engine.get_initial_response()
    │   │   └── response.create(tool_choice="none") → 挨拶音声生成
    │   └── audio_processor.start_capture()
    │
    ├── INPUT_AUDIO_BUFFER_SPEECH_STARTED (バージイン検出)
    │   ├── skip_pending_audio() → 再生中の音声をキャンセル
    │   └── response.cancel() → 生成中の応答をキャンセル
    │
    ├── INPUT_AUDIO_BUFFER_SPEECH_STOPPED
    │   └── "Processing..." 表示
    │
    ├── RESPONSE_CREATED
    │   └── _active_response = True
    │
    ├── RESPONSE_AUDIO_DELTA
    │   └── audio_processor.queue_audio(delta) → スピーカー再生
    │
    ├── CONVERSATION_ITEM_CREATED (type=FUNCTION_CALL)
    │   └── _pending_function_call = {name, call_id, previous_item_id}
    │
    ├── RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE
    │   └── _pending_function_call["arguments"] = event.arguments
    │
    ├── RESPONSE_DONE
    │   └── _execute_function_call() 実行 (後述)
    │
    ├── CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED
    │   └── ログ出力: "User: {transcript}"
    │
    ├── RESPONSE_AUDIO_TRANSCRIPT_DONE
    │   └── ログ出力: "Assistant: {transcript}"
    │
    └── ERROR
        └── エラーログ（キャンセル失敗は無視）
```

### 3.3 classify_and_act 関数呼び出しフロー

```
┌────────────┐     ┌───────────────────┐     ┌─────────────────┐
│   User     │     │  Azure Voice Live │     │  ScenarioClient │
│ (音声入力)  │     │  API (LLM+STT)    │     │  (Python)       │
└─────┬──────┘     └────────┬──────────┘     └────────┬────────┘
      │ 音声 (PCM16)        │                         │
      ├────────────────────▶│                         │
      │                     │ STT → テキスト化          │
      │                     │ LLM 推論                 │
      │                     │ classify_and_act 呼出    │
      │                     ├────────────────────────▶│
      │                     │ {intent, slots,          │
      │                     │  user_summary}           │
      │                     │                         │
      │                     │                  engine.process()
      │                     │                  ├── slot 更新
      │                     │                  ├── 安全チェック
      │                     │                  ├── 遷移評価
      │                     │                  └── 応答指示生成
      │                     │                         │
      │                     │◀────────────────────────┤
      │                     │ FunctionCallOutputItem   │
      │                     │ {response_instructions,  │
      │                     │  state, slots, missing}  │
      │                     │                         │
      │                     │ + ResponseCreateParams   │
      │                     │   (tool_choice="none")   │
      │                     │                         │
      │                     │ LLM が応答指示に基づき    │
      │                     │ 自然な応答テキスト生成     │
      │                     │ → TTS → 音声出力         │
      │◀────────────────────┤                         │
      │ 音声応答 (PCM16)     │                         │
```

**tool_choice の制御パターン:**

| タイミング              | tool_choice 値 | 理由                                      |
|----------------------|---------------|------------------------------------------|
| セッション設定 (デフォルト) | `REQUIRED`    | ユーザー発話のたびに必ず classify_and_act を呼ばせる |
| 挨拶送信時              | `"none"`      | 関数呼び出しなしで直接音声応答を生成させる            |
| classify_and_act 結果返却後 | `"none"`  | 関数結果をもとに音声応答を生成させる（再呼び出し防止）   |

---

## 4. 音声処理パイプライン

### 4.1 AudioProcessor

```
音声仕様:
  - フォーマット: PCM16 (16-bit signed integer)
  - サンプルレート: 24,000 Hz
  - チャンネル: モノラル (1ch)
  - チャンクサイズ: 1,200 サンプル = 50ms

デバイス選択優先度:
  1. 外部デバイス (USB マイク/ヘッドセット)
  2. ビルトインデバイス (MacBook 内蔵)
  ※ 仮想デバイス (Teams, Zoom, BlackHole 等) は除外
```

### 4.2 スレッドモデル

```
┌─────────────────────────────────────────────┐
│              Main Thread                     │
│  asyncio event loop                          │
│  ├── WebSocket 送受信                         │
│  ├── ScenarioEngine 処理                     │
│  └── Heartbeat (10秒間隔)                    │
├──────────────────────────────────────────────┤
│  PyAudio Capture Thread (callback)           │
│  └── PCM → base64 → run_coroutine_threadsafe │
│      → connection.input_audio_buffer.append  │
├──────────────────────────────────────────────┤
│  PyAudio Playback Thread (callback)          │
│  └── playback_queue → PCM → スピーカー出力     │
│      ├── seq_num によるパケット順序管理          │
│      └── skip_pending_audio() でバージイン対応  │
└──────────────────────────────────────────────┘
```

---

## 5. 状態遷移図（面接練習シナリオ）

```
                    ┌──────────────┐
                    │   greeting   │
                    │   (挨拶)      │
                    └──────┬───────┘
                           │ start_practice / provide_info
                           ▼
                    ┌──────────────────────┐
              ┌────▶│ collecting_preferences│◀─── change (confirm_setup から)
              │     │ (スロット収集)         │
              │     └──────────┬───────────┘
              │                │ provide_info + all_required_slots_filled
              │                ▼
              │     ┌──────────────────┐
              │     │  confirm_setup   │
              │     │  (設定確認)       │
              │     └────┬──────┬──────┘
              │          │      │ confirm + reset_question_count
              │   change │      ▼
              │          │  ┌──────────────┐
              └──────────┘  │ interviewing │◀──── next_question (feedback から)
                            │ (面接実施)    │◀──── skip + increment_question_count
                            └───┬──┬───┬───┘
                    answer +    │  │   │ end_interview
                  increment     │  │   │     OR
                                ▼  │   │ question_count >= 5
                 ┌────────────────┐│   │
                 │giving_feedback ││   │
                 │(フィードバック)  ││   ▼
                 └───┬──────┬─────┘│ ┌─────────┐
          next_      │      │      │ │ summary │
         question    │  end_│      │ │(総合評価) │
                     │interview    └▶└──┬───┬──┘
                     │      │           │   │
                     │      └───────────┘   │ end_interview
                     │                      ▼
                     │              ┌──────────┐
                     │              │ farewell  │ ← 終端状態
                     │              │ (終了)     │
                     │              └──────────┘
                     │
                     │  restart + clear_slots
                     └──────────────▶ greeting に戻る

  ※ distress インテントが検出された場合:
  ┌─────────────────────────────────────────┐
  │  greeting / collecting_preferences /     │
  │  confirm_setup / interviewing /          │
  │  giving_feedback / summary               │
  │         │ distress                       │
  │         ▼                                │
  │  ┌────────────┐                          │
  │  │ escalation │ ← 終端状態 (有人引継ぎ)   │
  │  └────────────┘                          │
  └─────────────────────────────────────────┘
```

---

## 6. 外部連携の仕組み

### 6.1 Azure AI Foundry Agent 連携 (`create_agent_with_voicelive.py`)

Voice Live API のセッション設定を Azure AI Foundry Agent のメタデータとして保存し、
Agent Store 経由で管理する方式。

```
┌──────────────────┐        ┌────────────────────┐
│  AIProjectClient │───────▶│ Azure AI Foundry    │
│  (azure-ai-      │        │ Agent Store         │
│   projects SDK)  │        │                     │
└──────────────────┘        │ ┌────────────────┐  │
                            │ │ Agent Metadata  │  │
                            │ │ ┌────────────┐ │  │
                            │ │ │ Voice Live │ │  │
                            │ │ │ Config     │ │  │
                            │ │ │ (chunked)  │ │  │
                            │ │ └────────────┘ │  │
                            │ └────────────────┘  │
                            └────────────────────┘
```

**メタデータチャンキング:** Azure Agent メタデータには 512 文字制限があるため、
Voice Live 設定 JSON を以下のキーで分割保存:

| キー                                      | 内容          |
|------------------------------------------|--------------|
| `microsoft.voice-live.configuration`     | 先頭 512 文字  |
| `microsoft.voice-live.configuration.1`   | 次の 512 文字  |
| `microsoft.voice-live.configuration.N`   | 以降繰り返し    |

### 6.2 Azure Speech Services 連携

| 機能                        | サービス                   | 設定箇所                        |
|---------------------------|--------------------------|-------------------------------|
| STT (音声→テキスト)          | Azure Speech (azure-speech) | `input_audio_transcription`   |
| TTS (テキスト→音声)          | Azure Neural Voice         | `AzureStandardVoice`          |
| VAD (発話区間検出)           | Azure Semantic VAD         | `AzureSemanticVadMultilingual`|
| ノイズ抑制                   | Azure Deep Noise Suppression | `input_audio_noise_reduction` |
| エコーキャンセル              | Server Echo Cancellation    | `input_audio_echo_cancellation`|
| カスタム辞書                  | SSML Lexicon (PLS)         | `custom_lexicon_url`          |
| 認識精度向上                  | Phrase List                | `phrase_list`                 |

### 6.3 カスタム発音辞書 (`lexicon.xml`)

PLS (Pronunciation Lexicon Specification) 形式のカスタム辞書ファイル。
Azure Speech TTS の発音を制御する。

```xml
<lexicon version="1.0" xml:lang="ja-JP"
         xmlns="http://www.w3.org/2005/01/pronunciation-lexicon">
  <lexeme>
    <grapheme>固有名詞</grapheme>
    <phoneme>カスタム発音</phoneme>
  </lexeme>
</lexicon>
```

### 6.4 Azure Identity / 認証

| 認証方式              | クラス                    | 用途            |
|---------------------|--------------------------| --------------- |
| API Key             | `AzureKeyCredential`     | 開発・テスト      |
| Azure CLI ログイン    | `AzureCliCredential`     | ローカル開発      |
| マネージド ID / SP    | `DefaultAzureCredential` | 本番運用         |

### 6.5 Interim Response（中間応答）

LLM の関数呼び出しや処理遅延時に、ユーザーへのフィラー応答を自動挿入する。

```
ユーザー発話 → STT → LLM 推論 → classify_and_act 呼出
                                 │
                                 ├── 500ms 超過 → "少々お待ちください。"
                                 ├── ツール実行中 → "確認しております。"
                                 │
                                 └── 本応答生成 → TTS → 音声出力
```

---

## 7. 拡張ポイント

### 7.1 新シナリオの追加

1. `scenario/scenarios/` に新しい YAML を作成
2. `--scenario` オプションでパスを指定して起動
3. コード変更不要

### 7.2 新アクションの追加

`ScenarioEngine._execute_actions()` にアクション名と処理を追加:

```python
elif action == "my_custom_action":
    # カスタムロジック
    pass
```

### 7.3 外部 API 連携の追加

`classify_and_act` に加えて追加の `FunctionTool` を定義し、
`_execute_function_call()` でディスパッチする:

```python
# 例: CRM 連携、データベース検索、外部 API 呼び出し
if function_name == "search_knowledge_base":
    result = await call_external_api(args)
```

### 7.4 条件式の拡張

`ScenarioEngine._evaluate_condition()` に新しい条件パターンを追加:

```python
if condition == "time_elapsed > 30min":
    # 経過時間チェック
    pass
```

---

## 8. テスト戦略

| テスト種別    | 対象                  | ファイル               |
|------------|----------------------|----------------------|
| ローダーテスト | YAML → ScenarioConfig パース・バリデーション | `tests/test_engine.py::TestLoader` |
| エンジンテスト | 状態遷移・スロット収集・安全ルール | `tests/test_engine.py::TestEngine` |
| 安全テスト    | SafetyEvaluator のルール評価 | `tests/test_engine.py::TestSafety` |

```bash
pytest tests/test_engine.py -v
```

---

## 9. 依存ライブラリ

| パッケージ               | バージョン     | 用途                           |
|------------------------|-------------|-------------------------------|
| `azure-ai-voicelive`  | `>=1.2.0b4` | Voice Live API クライアント      |
| `azure-ai-projects`   | `>=2.0.0b3` | Azure AI Foundry Agent 管理    |
| `azure-identity`      | latest      | Azure 認証                     |
| `pyaudio`             | latest      | ローカル音声入出力                 |
| `pyyaml`              | latest      | シナリオ YAML パース              |
| `python-dotenv`       | latest      | 環境変数読み込み                   |
| `openai`              | latest      | OpenAI 互換 API               |

---

## 10. 環境変数

| 変数名                           | 説明                          | 必須   |
|--------------------------------|------------------------------|-------|
| `AZURE_VOICELIVE_API_KEY`      | Voice Live API キー           | ※1    |
| `AZURE_VOICELIVE_ENDPOINT`     | Voice Live エンドポイント URL    | Yes   |
| `AZURE_VOICELIVE_MODEL`        | モデル名 (default: `gpt-realtime`) | No |
| `AZURE_VOICELIVE_VOICE`        | 音声名                         | No    |
| `AZURE_VOICELIVE_TEMPERATURE`  | 音声の温度パラメータ              | No    |
| `AZURE_VOICELIVE_RATE`         | 話速 (0.5-1.5)                | No    |
| `SCENARIO_PATH`                | シナリオ YAML パス              | No    |
| `PROJECT_ENDPOINT`             | AI Foundry プロジェクト EP      | ※2    |
| `MODEL_DEPLOYMENT_NAME`        | モデルデプロイ名                  | ※2    |
| `AGENT_NAME`                   | Agent 名                      | ※2    |

- ※1: `--use-token-credential` 使用時は不要
- ※2: `create_agent_with_voicelive.py` 使用時のみ必要
