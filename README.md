# CryptoTrust Agent 本機示範

此 Demo 使用真實 Core use cases 與 deterministic local fake adapters，完整執行：

`submit → preflight → start execution → formal-run orchestrator → artifact publication`

本機 Demo 不連線到 AWS 或外部 provider，也不需要 credential。

## 啟動

Windows PowerShell（從專案根目錄執行）：

```powershell
$env:PYTHONPATH = "src"
python -m uvicorn crypto_trust_agent.presentation.api.demo_ui_api:app --port 8000
```

開啟：

```text
http://127.0.0.1:8000/
```

首頁會建立僅供本機 Demo 使用的 `HttpOnly` session cookie。輸入問題、選擇一個幣種（市場狀態／假設驗證）或兩個幣種（資產比較），然後執行正式分析。完成後可查看：

- 最終分析報告（Final Report）
- 證據清單（Evidence List）
- 執行紀錄（Execution Log）
- 成果清冊（Manifest）
- 四份降級時最低成果組合下載

停止服務請在啟動 uvicorn 的終端按 `Ctrl+C`。

## HTTP 測試用 local token

自動化測試或手動 HTTP 呼叫可使用：

```text
Authorization: Bearer local-demo-user
```

此 token 只由 local fake verifier 接受，不是 production credential。Task、Execution 與 artifact 查詢都會檢查 verified principal ownership。

## 驗證

```powershell
$env:PYTHONPATH = "src"
python -B -m unittest tests.e2e.test_demo_ui -v
python -B -m unittest tests.integration.core.test_demo_ui_integration -v
```
