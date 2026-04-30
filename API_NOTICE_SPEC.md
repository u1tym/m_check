# 通知 API 仕様書

## 1. 概要

この API は、Web Push の購読情報を保存し、保存済みの購読情報に対して通知を送信するためのエンドポイントを提供します。

- 購読保存: `POST /save-subscription`
- 通知送信: `POST /notice`

どちらも認証 API と同じ FastAPI アプリ上に実装されます。

## 2. 前提条件

- DB に `public.accounts` と `notice.subscriptions` テーブルが存在すること
- `notice.subscriptions.aid` は `accounts.id` への外部キーであること
- `.env` に `VAPID_PRIVATE_KEY` が設定されていること

## 3. 環境変数

| 変数名 | 必須 | 説明 |
|---|---|---|
| `VAPID_PRIVATE_KEY` | はい | Web Push 送信用の VAPID 秘密鍵 |
| `VAPID_CLAIMS_SUB` | いいえ | `vapid_claims.sub` に設定する識別子（既定: `mailto:admin@example.com`） |

## 4. DB テーブル

### 4.1 `public.accounts`

通知 API では以下を利用します。

- `id`
- `username`
- `is_deleted`

`username` でユーザーを特定し、`is_deleted = false` のみ有効ユーザーとして扱います。

### 4.2 `notice.subscriptions`

| カラム | 型 | 必須 | 説明 |
|---|---|---|---|
| `aid` | integer | はい | `accounts.id` への外部キー、主キー |
| `subscription` | text | はい | PushSubscription を JSON 文字列として保存 |

## 5. API 仕様

### 5.1 `POST /save-subscription`

#### リクエスト

```json
{
  "username": "y-toyama",
  "subscription": {
    "endpoint": "https://fcm.googleapis.com/fcm/send/...",
    "expirationTime": null,
    "keys": {
      "p256dh": "...",
      "auth": "..."
    }
  }
}
```

#### 処理内容

1. `accounts` から `username` を検索（`is_deleted = false` のみ）
2. 見つからなければ `404`
3. `notice.subscriptions` を `aid=accounts.id` で検索
4. レコードがあれば `subscription` を更新、なければ新規追加（upsert）

#### レスポンス

- 成功: `200`

```json
{ "message": "ok" }
```

- 失敗: `404`

```json
{ "detail": "ユーザーが見つかりません" }
```

### 5.2 `POST /notice`

#### リクエスト

```json
{
  "username": "y-toyama",
  "title": "タイトル",
  "message": "通知内容",
  "url": "https://example.com/page"
}
```

#### 処理内容

1. `VAPID_PRIVATE_KEY` の存在を確認（未設定なら `500`）
2. `accounts` から `username` を検索（`is_deleted = false` のみ）
3. `notice.subscriptions` から `aid=accounts.id` を取得
4. 保存された `subscription`（JSON 文字列）をオブジェクト化
5. 以下 payload を作成して `webpush()` で送信

```json
{
  "title": "タイトル",
  "message": "通知内容",
  "url": "https://example.com/page"
}
```

#### レスポンス

- 成功: `200`

```json
{ "message": "ok" }
```

- 主な失敗パターン
  - `500`: `VAPID_PRIVATE_KEY` 未設定
  - `404`: ユーザーなし
  - `404`: subscription 未登録
  - `500`: DB 上の subscription JSON が不正
  - `502`: Web Push 送信失敗

## 6. 備考

- `notice.subscriptions` は `aid` 主キーのため、1ユーザーにつき保存できる購読情報は1件です。
- 複数デバイス対応が必要な場合は、主キー設計（例: `aid + endpoint`）の見直しが必要です。
