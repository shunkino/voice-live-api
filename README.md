# Voice Live API サンプル集

Azure AI Voice Live SDK を使用した音声対話エージェントのサンプルコード集です。YAML ベースのシナリオステートマシンによる会話制御や、Azure AI Evaluation SDK を使った評価パイプラインも含みます。

## 前提条件

- Python 3.10 以上
- Azure サブスクリプション
- Azure AI Services / Azure AI Foundry プロジェクト
- マイク・スピーカー（PyAudio 使用）

## セットアップ

```bash
# 仮想環境の作成・有効化
python -m venv .venv
source .venv/bin/activate

# 依存パッケージのインストール（Web モード / ブラウザ対話はこれだけで動作）
pip install -r requirements.txt
```

CLI モード（`--mode cli`）やルート直下のクイックスタートはマイク/スピーカーに
**PyAudio** を使います。PyAudio はネイティブの **PortAudio** ライブラリを必要とするため、
先にシステムへ PortAudio を入れてから `requirements-audio.txt` をインストールしてください
（Web モードでは不要です。ブラウザが音声を扱います）。

```bash
# Linux
sudo apt-get install -y portaudio19-dev libasound2-dev
# macOS
# brew install portaudio

pip install -r requirements-audio.txt
```

`.env` ファイルをプロジェクトルートに作成し、以下の環境変数を設定してください。

```env
AZURE_VOICELIVE_ENDPOINT=<Voice Live API のエンドポイント>
PROJECT_ENDPOINT=<Azure AI Foundry プロジェクトエンドポイント>
AGENT_NAME=<エージェント名（Agents 連携時）>
MODEL_DEPLOYMENT_NAME=gpt-realtime
```

API キーが無効化されているサブスクリプションでは、Azure CLI でサインインしたうえで `--use-token-credential` を付けて実行してください。

```powershell
az login
python voice-live-quickstart.py --use-token-credential
```

## Azure リソースのデプロイ

`infra/` ディレクトリの Bicep テンプレートで、このサンプルに必要な Azure リソースをデプロイできます。

デプロイされる主なリソース:

| リソース | 用途 |
|---|---|
| Azure AI Services | Voice Live API エンドポイント、`gpt-realtime` モデルデプロイ |
| Azure AI Hub | Foundry プロジェクトの Hub |
| Azure AI Project | `PROJECT_ENDPOINT` として使用 |
| Storage Account | AI Hub のバックエンド |
| Key Vault | AI Hub のシークレット管理 |

PowerShell では行継続にバッククォート `` ` `` を使います。

```powershell
az login

az group create `
  -n voicelivedemo `
  -l eastus2

$principalId = az ad signed-in-user show --query id -o tsv

az deployment group create `
  -g voicelivedemo `
  -f infra/main.bicep `
  -p infra/main.bicepparam `
  -p openAiUserPrincipalId=$principalId
```

デプロイ後、出力値を確認します。

```powershell
az deployment group show `
  -g voicelivedemo `
  -n main `
  --query properties.outputs `
  -o yaml
```

出力された値を `.env` に設定します。

```env
AZURE_VOICELIVE_ENDPOINT=<voiceLiveEndpoint の value>
PROJECT_ENDPOINT=<projectEndpoint の value>
AGENT_NAME=<agentName の value>
MODEL_DEPLOYMENT_NAME=<modelDeploymentName の value>
```

この Bicep テンプレートは `disableLocalAuth=true` を前提にしたキーレス構成です。API キーは出力されません。ローカル実行時は Azure CLI でサインインし、`--use-token-credential` を付けて実行してください。

## サンプル一覧

| ファイル / ディレクトリ | 概要 |
|---|---|
| `voice-live-quickstart.py` | Voice Live API の基本的な接続・音声対話 |
| `voice-live-function-call.py` | Function Calling を使った音声対話 |
| `voice-live-scenario.py` | YAML シナリオによるステートマシン制御付き音声対話 |
| `voice-live-agents-quickstart.py` | Azure AI Agents と Voice Live の連携 |
| `voice-live-experiments.py` | **カスタム音声 + フェイスアバター + MAI-Transcribe を統合したデモ** |
| `create_agent_with_voicelive.py` | Voice Live 設定付きエージェントの作成 |
| [`hosted-agents/weather-forecast/`](hosted-agents/weather-forecast/README.md) | **Foundry ホスト型エージェント – 日本語天気予報サンプル** (`invocations_ws` WebSocket, モック/JMA 対応) |

## 統合デモ: カスタム音声 + アバター + MAI-Transcribe

`voice-live-experiments.py` は、Voice Live API の高度な機能を **3 つまとめて 1 つの設定軸**
で扱う統合サンプルです。3 機能は個別のスクリプトではなく、共通の `ExperimentConfig`
から組み立てた単一の `RequestSession` に統合されています。

| 機能 | 内容 | 設定 |
|---|---|---|
| ① カスタム音声 | 標準 / HD / プロフェッショナルカスタム / パーソナル音声で応答。自分の声を再現する `personal` も可 | `--voice-type` |
| ② フェイスアバター | 話すアバターを WebRTC でブラウザに表示（Web モード） | `--avatar` |
| ③ MAI-Transcribe | 入力音声の文字起こしモデルを切り替えて比較（`mai-transcribe-1` 等） | `--transcription-model` |

### アーキテクチャ

共通コア（`voicelive_demo/`）を 2 つのフロントエンドが利用します。

```
voicelive_demo/
├── config.py            # ExperimentConfig（3 機能すべての設定）と引数/環境変数の解析
├── session_factory.py   # voice / transcription / avatar / animation → RequestSession を構築
├── audio.py             # PyAudio 入出力（CLI 用、共通実装）
├── cli_assistant.py     # CLI フロントエンド（マイク/スピーカー）
└── web/
    ├── server.py        # FastAPI：ブラウザ ⇄ Voice Live を中継、アバター SDP / viseme をリレー
    └── static/          # ブラウザクライアント（WebRTC アバター・ローカルフェイスモデル・字幕 UI）
        └── face.js      # viseme / blendshape → フェイスリグのマッピング層
```

- **CLI モード** (`--mode cli`): ターミナルでマイク/スピーカー対話。機能①③を試せます。
- **Web モード** (`--mode web`): FastAPI がブラウザと Voice Live を橋渡しし、機能②（アバター）を
  ①③に重ねて表示します。認証情報はサーバー側のみで保持されます。

アバターの WebRTC ネゴシエーション（SDP オファー/アンサー）はサーバー経由でリレーされ、
アバターの映像・音声は Azure からブラウザへ直接ストリームされます。マイク音声は
ブラウザで PCM16/24kHz に変換し、WebSocket 経由でサーバーが `input_audio_buffer` へ転送します。

### 実行例

```powershell
az login

# ① + ③: パーソナル音声（自分の声）+ MAI-Transcribe を CLI で試す
python voice-live-experiments.py --mode cli --use-token-credential `
  --voice-type personal --voice <パーソナル音声名> --voice-base-model DragonLatestNeural `
  --transcription-model mai-transcribe-1

# ②: アバターをブラウザに表示（① HD 音声・③ MAI-Transcribe と同時）
python voice-live-experiments.py --mode web --use-token-credential `
  --avatar --avatar-character lisa --avatar-style casual-sitting `
  --transcription-model mai-transcribe-1
# → http://127.0.0.1:8000 をブラウザで開く

# プロフェッショナルカスタム音声を使う場合
python voice-live-experiments.py --mode cli --use-token-credential `
  --voice-type custom --voice <カスタム音声名> --voice-endpoint-id <デプロイ GUID>
```

### ⑤ Foundry ホスト型エージェントに接続（Web / CLI）

`--model`（素のモデル）の代わりに、Foundry にデプロイした **ホスト型エージェント** へ接続できます。
Voice Live が音声認識（STT）と音声合成（TTS）を担当し、会話ロジックはホスト型エージェントが処理します
（例: `hosted-agents/weather-forecast` の天気予報エージェント）。

前提として、エージェントは **Voice Live 互換** である必要があります（`invocations` プロトコルを公開し、
バージョンメタデータに `voiceLiveCompatible: "true"` を設定。詳細は `hosted-agents/weather-forecast/README.md`）。

```powershell
az login

# Web UI からホスト型エージェントに接続（ブラウザでマイク対話）
python voice-live-experiments.py --mode web --use-token-credential `
  --endpoint https://<account>.services.ai.azure.com `
  --agent-name weather-forecast-agent --agent-project-name weather-agent-proj
# → http://127.0.0.1:8000 を開く。ヘッダーに「🤖 Hosted agent: ...」バッジが表示される

# CLI からホスト型エージェントに接続（ターミナルでマイク対話）
python voice-live-experiments.py --mode cli --use-token-credential `
  --endpoint https://<account>.services.ai.azure.com `
  --agent-name weather-forecast-agent --agent-project-name weather-agent-proj
```

環境変数で指定する場合は `AZURE_VOICELIVE_AGENT_NAME` と `AZURE_VOICELIVE_AGENT_PROJECT` を設定します。
両方を設定したときのみエージェントモードになり、片方だけの場合は起動時にエラーになります。

主なオプション（`--help` で全件表示）:

| オプション | 説明 |
|---|---|
| `--mode {cli,web}` | 実行モード（既定: cli） |
| `--agent-name` / `--agent-project-name` | Foundry **ホスト型エージェント**に接続（`--model` の代わり）。両方指定が必要 |
| `--voice-type {standard,personal,custom,avatar-voice-sync}` | 音声タイプ |
| `--voice` / `--voice-base-model` / `--voice-endpoint-id` | 音声名 / パーソナル基盤モデル / カスタム音声 GUID |
| `--transcription-model` | `azure-speech` / `mai-transcribe-1` / `whisper-1` / `gpt-4o-transcribe` ほか |
| `--avatar` / `--avatar-type` / `--avatar-character` / `--avatar-style` | アバター設定（Web モード） |
| `--viseme` | リップシンク用の viseme 出力を要求（ローカルフェイスモデルの口の動き） |
| `--blendshapes` | 3D ブレンドシェイプ出力を要求（眉・まばたき・口角などの表情） |

### ④ ローカルフェイスモデル（自前モデルに表情をマッピング）

サーバー側でレンダリングされる動画アバター（機能②）の代わりに、**ブラウザ内で
自前のフェイスモデルをレンダリング**し、Voice Live API が返す顔アニメーション
キュー（viseme / blendshape）をそのモデルのリグにマッピングできます。Azure は
音声とアニメーションの手がかりだけを提供し、描画はブラウザ側で行います。

- `--avatar` を **付けず**に `--viseme`（および/または `--blendshapes`）を指定すると、
  Web クライアントが軽量な 2D フェイス（SVG）を表示します。
- **viseme ID（0〜21）** は標準 Azure Speech viseme セットで、口の開き・横幅・
  丸めにマッピングされ、`audio_offset_ms` で音声再生に同期します。
- **blendshapes**（ARKit 互換チャンネル, 60fps）が有効な場合は、眉・まばたき・
  笑顔なども含めた表情を駆動します。
- マッピング層は `voicelive_demo/web/static/face.js`（`visemeToShape` /
  `blendshapesToRig`）に分離されているため、同じアニメーションストリームを
  Three.js / Unity / Unreal などの自前リグに差し替えることも容易です。

```powershell
az login

# 自前フェイスモデルを viseme + blendshapes で駆動（アバターは使わない）
python voice-live-experiments.py --mode web --use-token-credential `
  --viseme --blendshapes
# → http://127.0.0.1:8000 を開き、「Start conversation」で話しかける
```

> **注意**
> - パーソナル音声・プロフェッショナルカスタム音声・カスタムアバターは
>   [制限付きアクセス](https://aka.ms/customneural) です。利用にはフォーム申請が必要です。
> - カスタムモデルは Voice Live を呼び出すのと同じ Foundry リソース上に存在する必要があります。
> - `mai-transcribe-1` では phrase list / custom speech は利用できません（フェアな比較のため自動的に無効化されます）。
> - アバターは限定リージョンでのみ利用可能です。

設定は環境変数でも指定できます。`.env.example` をコピーして `.env` を作成してください。

## シナリオエンジン

`scenario/` ディレクトリに、YAML 定義のステートマシンで会話フローを制御するエンジンがあります。

- **`scenario/models.py`** — データモデル（スロット、インテント、状態遷移、安全ルール等）
- **`scenario/engine.py`** — ステートマシンエンジン
- **`scenario/loader.py`** — YAML ローダー
- **`scenario/safety.py`** — 安全ルール評価
- **`scenario/scenarios/`** — シナリオ YAML 定義ファイル

### シナリオ実行例

```bash
# デフォルトシナリオ（面接練習）で起動
python voice-live-scenario.py

# シナリオファイルを指定して起動
python voice-live-scenario.py --scenario scenario/scenarios/interview_practice.yaml
```

## 評価パイプライン

`evaluation/` ディレクトリに、Azure AI Evaluation SDK を使った評価ツールがあります。

| ファイル | 概要 |
|---|---|
| `evaluation/dataset_generator.py` | シナリオ YAML から評価用データセット (JSONL) を生成 |
| `evaluation/run_eval.py` | Foundry SDK を使ったローカル評価実行 |
| `evaluation/log_harvester.py` | 実行ログからの評価データ抽出 |
| `evaluation/run_batch_eval.py` | バッチ評価実行 |

### 評価の実行例

```bash
# データセット生成
python evaluation/dataset_generator.py --scenario scenario/scenarios/interview_practice.yaml

# 評価実行（Relevance + TaskAdherence）
python evaluation/run_eval.py \
  -d .foundry/datasets/interview_practice-eval-seed-v1.jsonl \
  -e relevance task_adherence
```

## ディレクトリ構成

```
├── voice-live-quickstart.py          # クイックスタート
├── voice-live-function-call.py       # Function Calling サンプル
├── voice-live-scenario.py            # シナリオ制御サンプル
├── voice-live-agents-quickstart.py   # Agents 連携サンプル
├── voice-live-experiments.py         # 統合デモ（カスタム音声+アバター+MAI-Transcribe）
├── create_agent_with_voicelive.py    # エージェント作成スクリプト
├── requirements.txt
├── .env.example                      # 環境変数テンプレート
├── lexicon.xml                       # TTS 発音辞書
├── voicelive_demo/                   # 統合デモの共通コア
│   ├── config.py                     # ExperimentConfig / 引数・環境変数解析
│   ├── session_factory.py            # RequestSession 構築（3 機能の統合点）
│   ├── audio.py                      # PyAudio 入出力（CLI 用）
│   ├── cli_assistant.py              # CLI フロントエンド
│   └── web/
│       ├── server.py                 # FastAPI 中継サーバー
│       └── static/                   # ブラウザクライアント（WebRTC アバター）
├── scenario/
│   ├── engine.py
│   ├── loader.py
│   ├── models.py
│   ├── safety.py
│   └── scenarios/
│       └── interview_practice.yaml   # 面接練習シナリオ
├── evaluation/
│   ├── dataset_generator.py
│   ├── run_eval.py
│   ├── log_harvester.py
│   └── run_batch_eval.py
├── docs/
│   └── scenario-control-design.md    # シナリオ制御設計書
└── tests/
    ├── test_engine.py
    └── test_session_factory.py       # 統合デモの設定ロジックのテスト
```