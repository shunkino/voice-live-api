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

# 依存パッケージのインストール
pip install -r requirements.txt
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

## サンプル一覧

| ファイル | 概要 |
|---|---|
| `voice-live-quickstart.py` | Voice Live API の基本的な接続・音声対話 |
| `voice-live-function-call.py` | Function Calling を使った音声対話 |
| `voice-live-scenario.py` | YAML シナリオによるステートマシン制御付き音声対話 |
| `voice-live-agents-quickstart.py` | Azure AI Agents と Voice Live の連携 |
| `create_agent_with_voicelive.py` | Voice Live 設定付きエージェントの作成 |

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
├── create_agent_with_voicelive.py    # エージェント作成スクリプト
├── requirements.txt
├── lexicon.xml                       # TTS 発音辞書
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
    └── test_engine.py
```