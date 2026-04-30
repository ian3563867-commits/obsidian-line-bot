_LIFE_KEYWORDS = ("生活", "life", "個人", "personal")


def is_life(prompt: str) -> bool:
    lower = prompt.lower()
    return any(k in lower for k in _LIFE_KEYWORDS)


def build_vault_prompt(prompt: str, allow_write: bool = False) -> str:
    life = is_life(prompt)
    if allow_write:
        execution_rule = (
            "【執行要求】你現在不是在確認規則，而是要立刻完成使用者問題。\n"
            "禁止只回答「收到」、「我會遵守」或重述規則；必須實際查詢、整理或寫入。\n"
            "本次已由系統確認為寫入模式，允許新增 vault 筆記。\n\n"
        )
    else:
        execution_rule = (
            "【執行要求】你現在不是在確認規則，而是要立刻完成使用者問題。\n"
            "禁止只回答「收到」、「我會遵守」或重述規則；必須實際查詢或整理回答。\n"
            "本次是查詢 / 討論模式，不是寫入模式。禁止建立、修改、刪除或移動任何 vault 檔案；"
            "即使使用者輸入看起來像事件紀錄，也只能查詢或討論，不能寫入 vault。\n\n"
        )

    if life:
        classification_rule = (
            "【本次問題分類】life=True。\n"
            "因使用者訊息包含生活/Life/life/個人/personal 關鍵字，若寫入 vault，frontmatter tags 必須包含 life。\n\n"
        )
        query_steps = (
            "【查詢 vault 必須執行的步驟 - 保守三層搜尋實驗版】\n"
            "Step 1: Read 04_Knowledge/index.md 全文，找出與問題相關的知識頁面或高價值原始入口。\n"
            "        若 index 有明確命中，必須 Read 對應的 04_Knowledge 知識頁或 index 指向的原始入口；不要只看 index 摘要就回答。\n"
            "Step 2: 判斷是否需要 Grep 原始資料。只有以下條件任一成立，才 Grep 搜尋 02_Projects/ 和 00_Inbox/ 資料夾：\n"
            "        • index 無命中或命中不足\n"
            "        • 已讀取的知識頁仍不足以回答問題，缺少原因、解法、日期、決策人、操作步驟或狀態\n"
            "        • 使用者問題包含時間或狀態詞：最新、最近、今天、本週、目前、目前狀況、還沒解決、現在、上次、剛剛\n"
            "        • 使用者問題包含具體識別碼：問題編號、任務編號、PCN、工單號、DN單號、棧板號\n"
            "        • 使用者問題是在查 MSG、JSON、API、SQL、模板或範例\n"
            "        • index 命中的知識頁超過 14 天未更新，且問題屬於異常、問題追蹤或 open item 類\n"
            "        • 使用者明確要求完整搜尋、原始紀錄或會議紀錄\n"
            "        不確定是否足夠時，預設執行 Grep。\n"
            "Step 3: 若 Step 2 有執行 Grep，Read 所有命中的檔案；若 Step 2 未執行，Read Step 1 命中的知識頁或原始入口。\n"
            "Step 4: 整合所有已讀資料後回答，結尾列出所有實際參考來源檔案路徑。\n\n"
        )
    else:
        classification_rule = (
            "【本次問題分類】life=False。\n"
            "查詢與寫入時請排除 frontmatter tags 含 life 的檔案，除非使用者明確要求生活/個人內容。\n\n"
        )
        query_steps = (
            "【查詢 vault 必須執行的步驟 - 保守三層搜尋實驗版】\n"
            "Step 1: Read 04_Knowledge/index.md 全文，找出與問題相關的知識頁面或高價值原始入口。\n"
            "        若 index 有明確命中，必須 Read 對應的 04_Knowledge 知識頁或 index 指向的原始入口；不要只看 index 摘要就回答。\n"
            "Step 2: 判斷是否需要 Grep 原始資料。只有以下條件任一成立，才 Grep 搜尋 02_Projects/ 資料夾（關鍵字從問題中抽取）：\n"
            "        • index 無命中或命中不足\n"
            "        • 已讀取的知識頁仍不足以回答問題，缺少原因、解法、日期、決策人、操作步驟或狀態\n"
            "        • 使用者問題包含時間或狀態詞：最新、最近、今天、本週、目前、目前狀況、還沒解決、現在、上次、剛剛\n"
            "        • 使用者問題包含具體識別碼：問題編號、任務編號、PCN、工單號、DN單號、棧板號\n"
            "        • 使用者問題是在查 MSG、JSON、API、SQL、模板或範例\n"
            "        • index 命中的知識頁超過 14 天未更新，且問題屬於異常、問題追蹤或 open item 類\n"
            "        • 使用者明確要求完整搜尋、原始紀錄或會議紀錄\n"
            "        不確定是否足夠時，預設執行 Grep。\n"
            "Step 3: 若 Step 2 有執行 Grep，Read 所有命中的檔案（包含 04_Knowledge 知識頁、02_Projects 原始紀錄），並跳過 frontmatter tags 含 life 的檔案。\n"
            "        若 Step 2 未執行，Read Step 1 命中的知識頁或原始入口。\n"
            "Step 4: 整合所有已讀資料後回答，結尾列出所有實際參考來源檔案路徑。\n\n"
        )

    if allow_write:
        write_rule = (
            "【寫入規則】若需要寫入 vault，一律存到 00_Inbox/ 資料夾。\n"
            "若使用者訊息含有「生活/Life/life/個人/personal」，"
            "frontmatter tags 必須包含 life。\n\n"
        )
    else:
        write_rule = (
            "【寫入規則】本次禁止寫入 vault。\n"
            "若使用者內容需要沉澱成筆記，只能在回答中建議使用「回報問題」或「紀錄：」重新觸發寫入模式，"
            "不得自行新增到 00_Inbox 或任何正式筆記資料夾。\n\n"
        )

    format_rule = (
        "【回覆格式】純文字，不要用 Markdown（不用 **粗體**、不用表格、不用 # 標題）。"
        "用「─」分隔區塊、用「•」列點、欄位用「：」對齊。"
        "因為訊息會顯示在 LINE，Markdown 不會渲染。\n"
        "若使用者查詢 MSG、JSON、API、SQL、模板或範例，必須完整保留命中的連續程式碼/JSON區塊，"
        "不可只摘要 EventID 或省略 Header、Body、KeepData、欄位清單；若同一檔案有多個相關模板，需逐一完整列出。\n"
        "MSG、JSON、API payload、SQL、指令或程式碼必須使用 Markdown fenced code block 包起來，"
        "例如 ```json、```sql 或 ```text，方便結果頁排版與複製。\n\n"
    )

    return execution_rule + classification_rule + query_steps + write_rule + format_rule + "【使用者問題】\n" + prompt
