# 天気予報エージェント (Weather Forecast Agent)

Microsoft Foundry の **ホスト型音声エージェント** を使った日本語天気予報サンプルです。
`invocations_ws` WebSocket プロトコルを介してテキスト・音声リクエストを受け付け、
日本語の天気予報スタイルで応答します。

---

## 目次

1. [前提条件](#前提条件)
2. [セットアップ](#セットアップ)
3. [ローカルモック検証](#ローカルモック検証)
4. [テキストクライアントによる動作確認](#テキストクライアントによる動作確認)
5. [設定項目一覧](#設定項目一覧)
6. [デモデータ vs ライブ気象データ](#デモデータ-vs-ライブ気象データ)
7. [Foundry ホスト型エンドポイント](#foundry-ホスト型エンドポイント)
8. [ディレクトリ構成](#ディレクトリ構成)

---

## 前提条件

| 要件 | 備考 |
|------|------|
| Python 3.10+ | |
| Azure CLI | Foundry/Voice Live エンドポイントへの接続時 (`az login`) |
| Microsoft Foundry プロジェクト | `invocations_ws` プレビュー対応リージョン (North Central US) |
| マイク・スピーカー | エンドツーエンドの音声検証時のみ必要 |

ローカルのテキスト WebSocket 検証だけであれば Azure アカウントは不要です。

---

## セットアップ

```bash
cd hosted-agents/weather-forecast

# 仮想環境の作成・有効化
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 依存パッケージのインストール
pip install -r requirements.txt

# 環境変数ファイルの作成
cp .env.example .env
```

ローカル実行だけであれば `.env` の編集は不要です（デフォルト値で動作します）。
Foundry ホスト型エンドポイントを使う場合は [設定項目一覧](#設定項目一覧) を参照してください。

---

## ローカルモック検証

```bash
# テストの実行
pytest

# ローカルサーバーの起動
python -m agent.app
```

起動成功時:

- `127.0.0.1:8080` でサーバーが起動します
- 設定値が不足している場合は、不足している項目名が表示されます
- モック天気リクエストに対して日本語の天気予報スタイルで応答します

---

## テキストクライアントによる動作確認

サーバーが起動した状態で、別ターミナルから以下を実行します:

```bash
# 今日の東京の天気を尋ねる（weather.response が返る）
python client/text_client.py --text "今日の東京の天気は？"

# 場所なしで天気を尋ねる（weather.clarification が返る）
python client/text_client.py --text "今日の天気は？"

# 大阪の明日の天気を尋ねる
python client/text_client.py --text "大阪の明日の天気を教えてください"

# セッション ID を指定してフォローアップ会話を続ける
python client/text_client.py --text "明日は？" --session my-session-1

# カスタムホスト / ポートを指定する場合
python client/text_client.py --text "今日の東京の天気は？" --host 127.0.0.1 --port 8080

# websockets バックエンドを使う場合（要 pip install websockets）
python client/text_client.py --text "今日の東京の天気は？" --backend websockets
```

### 応答フォーマット例

**weather.response**（天気情報あり）:
```json
{
  "type": "weather.response",
  "text": "東京の今日の天気は晴れ時々くもりです。最高気温は26度前後の見込みです。",
  "location": "東京",
  "day": "今日",
  "demo_data": true
}
```

**weather.clarification**（場所不明）:
```json
{
  "type": "weather.clarification",
  "text": "どの地域の天気を知りたいですか？",
  "missing": ["location"]
}
```

---

## 設定項目一覧

### ローカルサンプル設定

| 変数名 | 必須 | デフォルト | 説明 |
|--------|------|-----------|------|
| `WEATHER_AGENT_NAME` | No | `weather-forecast-agent` | エージェントの表示名（情報のみ） |
| `WEATHER_PROVIDER` | No | `mock` | `mock`（デモデータ）/ `live`（Open-Meteo ライブデータ）/ `jma`（スケルトン） |
| `RESPONSE_MODE` | No | `template` | `template`（ルールベース）/ `llm`（モデル + get_weather ツール） |
| `LLM_MODEL_DEPLOYMENT` | No | `gpt-4.1-mini` | `RESPONSE_MODE=llm` で使うチャットモデルのデプロイ名 |
| `AZURE_AI_PROJECT_ENDPOINT` | No | なし | ローカルで LLM を使う際の Foundry プロジェクトエンドポイント（ホスト環境では `FOUNDRY_PROJECT_ENDPOINT` を自動注入） |
| `WEATHER_AGENT_HOST` | No | `127.0.0.1` | ローカルサーバーホスト |
| `WEATHER_AGENT_PORT` | No | `8080` | ローカルサーバーポート |

### Foundry / Voice Live デプロイ設定

| 変数名 | 必須 | デフォルト | 説明 |
|--------|------|-----------|------|
| `PROJECT_NAME` | **Yes** | なし | Foundry プロジェクト名 |
| `AGENT_NAME` | **Yes** | なし | ホスト型エージェント名 |
| `AZURE_VOICELIVE_ENDPOINT` | **Yes** | なし | Voice Live / Foundry リソースエンドポイント |
| `MODEL_DEPLOYMENT_NAME` | **Yes** | `gpt-realtime` | 音声エージェントが使用するモデルデプロイ名 |
| `VOICE_NAME` | No | `ja-JP-NanamiNeural` | 音声応答に使用する日本語音声 |
| `AZURE_VOICELIVE_TRANSCRIPTION_LANGUAGE` | No | `ja` | 音声認識の主言語 |

不足している必須項目がある場合、サーバー起動時に項目名を明示してエラーを報告します。

---

## デモデータ vs ライブ気象データ / LLM モード

天気データの取得方法（`WEATHER_PROVIDER`）と、応答の生成方法（`RESPONSE_MODE`）は独立して設定できます。

| `WEATHER_PROVIDER` | データの種類 | 追加設定 |
|--------------------|-------------|---------|
| `mock`（デフォルト） | 決め打ちのデモ予報。東京・大阪・京都・札幌・福岡の5都市に対応。`demo_data: true`。 | 不要（オフライン可） |
| `live` | [Open-Meteo](https://open-meteo.com/) のライブ予報（無料・キー不要・全世界）。日本の主要都市は内蔵座標で解決し、その他は Open-Meteo ジオコーディングで解決。`demo_data: false`。 | ネットワーク接続 |
| `jma` | 気象庁 API のスケルトン（未実装）。失敗時は `mock` にフォールバック。 | — |

| `RESPONSE_MODE` | 応答の生成 |
|-----------------|-----------|
| `template`（デフォルト） | キーワード解析によるルールベース応答。オフライン・決定的でテスト向き。 |
| `llm` | Foundry チャットモデル（Responses API）が `get_weather` ツール（function calling）を呼び出して動的に天気を取得し、ユーザーの言語で自然に応答。失敗時は `template` に自動フォールバック。 |

### 日本語 / 英語（バイリンガル）

入力言語を自動判定し、同じ言語で応答します（**日本語優先**）。`template` / `llm` の両モードに対応し、
天気予報・聞き返し・天気以外のお断りもすべて日本語/英語で出し分けます。

### LLM + ツール構成の例（ライブ天気を動的に取得）

```env
RESPONSE_MODE=llm
WEATHER_PROVIDER=live
LLM_MODEL_DEPLOYMENT=gpt-4.1-mini
# ローカル実行時のみ（ホストでは自動注入）
AZURE_AI_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
```

この構成では、モデルが `get_weather(location, day)` ツールを呼び、ツールが Open-Meteo から
ライブ予報を取得してモデルに返し、モデルが自然な話し言葉（日本語/英語）で読み上げ用テキストを生成します。
`mock` データ利用時のみ `demo_data: true` になります。

---

## Foundry ホスト型エンドポイント

コンテナを Microsoft Foundry にホスト型エージェントとしてデプロイした後、
以下の WebSocket URL で接続します（Microsoft Entra 認証が必要）:

```text
wss://<account>.services.ai.azure.com/api/projects/agents/endpoint/protocols/invocations_ws
  ?project_name=<project>
  &agent_name=<agent>
  &agent_session_id=demo-session-1
  &foundry_features=HostedAgents=V1Preview
```

接続成功時の確認事項:

- WebSocket アップグレードが Microsoft Entra 認証で成功する
- 対応都市（東京・大阪・京都・札幌・福岡）への天気質問が日本語で応答される
- 同じ `agent_session_id` で再接続するとアクティブセッション中の会話コンテキストが保持される

### Azure CLI での認証

```bash
az login

# トークンを取得してヘッダーに付与する例
TOKEN=$(az account get-access-token --resource "https://cognitiveservices.azure.com/" --query accessToken -o tsv)
```

### デプロイ対応リージョン

`invocations_ws` プレビュー機能は現在 **North Central US** リージョンのみ対応しています。

### コンテナイメージのビルドと起動

```bash
# ローカルビルド確認
docker build -t weather-forecast-agent .
docker run -p 8080:8080 --env-file .env weather-forecast-agent

# Foundry へのデプロイ（Dockerfile 配置後）
# az acr build --registry <registry> --image weather-forecast-agent:latest .
```

---

## ディレクトリ構成

```
hosted-agents/weather-forecast/
├── README.md                 # このファイル
├── requirements.txt          # 依存パッケージ
├── .env.example              # 環境変数テンプレート
├── Dockerfile                # ホスト型エージェント用コンテナ定義
├── agent/
│   ├── __init__.py
│   ├── app.py                # WebSocket サーバー本体
│   ├── config.py             # 設定の読み込みと検証
│   ├── protocol.py           # WebSocket メッセージ定義
│   ├── session_state.py      # セッション状態管理（インメモリ）
│   ├── weather.py            # 天気プロバイダー（mock / live[Open-Meteo] / jma）+ バイリンガル
│   └── llm.py                # LLM 応答生成（Responses API + get_weather ツール）
├── client/
│   └── text_client.py        # テキスト WebSocket 検証クライアント
└── tests/
    ├── test_config.py
    ├── test_protocol.py
    ├── test_session_state.py
    ├── test_weather.py
    ├── test_llm_bilingual.py
    └── test_readme_contract.py
```
