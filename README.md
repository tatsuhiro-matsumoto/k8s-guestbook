# k8s-guestbook

Kubernetes（NKP など）のデモ用、シンプルな記録帳アプリです。

- **AP**：Python Flask（Deployment、既定3台）
- **DB**：PostgreSQL 17（StatefulSet ＋ PVC）
- 画面に「**このページを返した Pod 名**」が色付きで出るので、リロードや台数変更で振り分けが目に見えます
- 各書き込みに「書き込んだ Pod 名」も記録されます

## 1. GitHub に置く（初回だけ）

1. GitHub で `k8s-guestbook` という空リポジトリを作る
2. このフォルダの中身を push する
   ```
   git init && git add . && git commit -m "first commit"
   git branch -M main
   git remote add origin https://github.com/tatsuhiro-matsumoto/k8s-guestbook.git
   git push -u origin main
   ```
3. GitHub Actions が自動でイメージをビルドし、`ghcr.io/tatsuhiro-matsumoto/k8s-guestbook:latest` に push します（Actions タブで確認）
4. **イメージを公開にする**：GitHub のプロフィール → Packages → k8s-guestbook → Package settings → Change visibility → Public
   （非公開のままだと、クラスタから pull するのに認証が必要になります）

以降は毎週月曜に自動で再ビルドされ、ベースイメージの脆弱性修正が取り込まれます。

## 2. デプロイ

GitHub Actions のビルドが終わり、イメージを Public にしたら：

```
kubectl apply -f k8s/guestbook.yaml
kubectl get pods -w              # 全部 Running になるまで待つ
kubectl get svc frontend         # EXTERNAL-IP をブラウザで開く
```

## 3. デモの流れ

| やること | コマンド | 見せどころ |
|---|---|---|
| 構成を見る | `kubectl get all` | Deployment → ReplicaSet → Pod の親子関係 |
| リロードする | （ブラウザ） | 返す Pod 名と色が変わる＝負荷分散 |
| 台数を増やす | `kubectl scale deployment frontend --replicas=5` | Pod が増え、画面に新しい Pod 名が出る |
| Pod を消す | `kubectl delete pod <frontendのPod名>` | すぐ新しい Pod が作られる（自己修復） |
| DB Pod を消す | `kubectl delete pod postgres-0` | 戻ってきても書き込みが残っている（PVC で永続化） |
| ストレージを見る | `kubectl get pvc` | PVC の実体は Nutanix のボリューム（Prism で確認） |

## 4. 片付け

```
kubectl delete -f k8s/guestbook.yaml
kubectl delete pvc data-postgres-0   # DB のデータも消す場合
```

## メモ

- DB パスワードは `guestbook.yaml` の Secret（`change-me`）。デモ以外で使うなら変更してください
- PVC は既定の StorageClass を使います。指定したい場合は `volumeClaimTemplates` に `storageClassName` を追加
- **エアギャップ環境**：`ghcr.io/tatsuhiro-matsumoto/k8s-guestbook:latest` と `postgres:17` を社内レジストリ（Harbor など）にコピーし、`image:` をそちらに書き換えれば動きます
- このリポジトリを fork して自分のイメージを使う場合は、`k8s/guestbook.yaml` の `image:` を自分の `ghcr.io/<ユーザー名>/k8s-guestbook:latest` に変更
- ローカルで試す：`docker build -t k8s-guestbook .`
