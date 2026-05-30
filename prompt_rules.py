_LIFE_KEYWORDS = ("生活", "life", "個人", "personal")


def is_life(prompt: str) -> bool:
    lower = prompt.lower()
    return any(k in lower for k in _LIFE_KEYWORDS)


def build_vault_prompt(prompt: str, allow_write: bool = False, life_override: bool = False) -> str:
    life = is_life(prompt) or life_override
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
        life_reason = (
            "本次經 Python pre-check 嚴格命中，確認為使用者主動查生活內容，允許讀取候選 life 檔案。"
            if life_override
            else "因使用者訊息包含生活/Life/life/個人/personal 關鍵字，若寫入 vault，frontmatter tags 必須包含 life。"
        )
        classification_rule = (
            "【本次問題分類】life=True。\n"
            f"{life_reason}\n\n"
        )
        query_steps = (
            "【查詢 vault 必須執行的步驟 - 保守三層搜尋實驗版】\n"
            "Step 1: Read 04_Knowledge/index.md 全文，找出與問題相關的知識頁面或高價值原始入口。\n"
            "        若 index 有明確命中，必須 Read 對應的 04_Knowledge 知識頁或 index 指向的原始入口；不要只看 index 摘要就回答。\n"
            "        若知識頁頂部或來源區塊標示原始來源檔，且使用者是在查「內容、完整、流程、步驟、SOP、實作、自動化」這類細節問題，必須一併 Read 原始來源檔。\n"
            "Step 2: 判斷是否需要 Grep 原始資料。只有以下條件任一成立，才 Grep 搜尋 02_Projects/ 和 00_Inbox/ 資料夾：\n"
            "        • index 無命中或命中不足\n"
            "        • 已讀取的知識頁仍不足以回答問題，缺少原因、解法、日期、決策人、操作步驟或狀態\n"
            "        • 使用者問題包含時間或狀態詞：最新、最近、今天、本週、目前、目前狀況、還沒解決、現在、上次、剛剛\n"
            "        • 使用者問題包含具體識別碼：問題編號、任務編號、PCN、工單號、DN單號、棧板號\n"
            "        • 使用者問題是在查 MSG、JSON、API、SQL、模板或範例\n"
            "        • index 命中的知識頁超過 14 天未更新，且問題屬於異常、問題追蹤或 open item 類\n"
            "        • 使用者明確要求完整搜尋、原始紀錄或會議紀錄\n"
            "        • 使用者是在做廣泛主題查詢，例如：所有、全部、相關內容、內容、有哪些、整理；這類問題不可只靠 index hints 就宣稱「所有」\n"
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
            "        若知識頁頂部或來源區塊標示原始來源檔，且使用者是在查「內容、完整、流程、步驟、SOP、實作、自動化」這類細節問題，必須一併 Read 原始來源檔。\n"
            "Step 2: 判斷是否需要 Grep 原始資料。只有以下條件任一成立，才 Grep 搜尋 02_Projects/ 資料夾（關鍵字從問題中抽取）：\n"
            "        • index 無命中或命中不足\n"
            "        • 已讀取的知識頁仍不足以回答問題，缺少原因、解法、日期、決策人、操作步驟或狀態\n"
            "        • 使用者問題包含時間或狀態詞：最新、最近、今天、本週、目前、目前狀況、還沒解決、現在、上次、剛剛\n"
            "        • 使用者問題包含具體識別碼：問題編號、任務編號、PCN、工單號、DN單號、棧板號\n"
            "        • 使用者問題是在查 MSG、JSON、API、SQL、模板或範例\n"
            "        • index 命中的知識頁超過 14 天未更新，且問題屬於異常、問題追蹤或 open item 類\n"
            "        • 使用者明確要求完整搜尋、原始紀錄或會議紀錄\n"
            "        • 使用者是在做廣泛主題查詢，例如：所有、全部、相關內容、內容、有哪些、整理；這類問題不可只靠 index hints 就宣稱「所有」\n"
            "        不確定是否足夠時，預設執行 Grep。\n"
            "Step 3: 若 Step 2 有執行 Grep，Read 所有命中的檔案（包含 04_Knowledge 知識頁、02_Projects 原始紀錄），並跳過 frontmatter tags 含 life 的檔案。\n"
            "        若 Step 2 未執行，Read Step 1 命中的知識頁或原始入口。\n"
            "Step 4: 整合所有已讀資料後回答，結尾列出所有實際參考來源檔案路徑。\n\n"
        )

    precheck_rule = (
        "【Pre-check 覆寫規則】若使用者問題中明確出現「Python deterministic index pre-check 先完成文件級縮範圍」"
        "或「候選文件（請限定在這些檔案內查詢）」，代表 Python 已先完成入口判斷。\n"
        "此時必須優先遵守候選文件限制：只在候選文件內 Read / 搜尋 / 判斷段落，不要因一般三層搜尋規則或 MSG/SQL/API 條款而全 vault Grep。\n"
        "只有在候選文件明顯不足時，才可在回答中說明需要 fallback；不要自行擴大到其他檔案。\n\n"
    )

    answer_synthesis_rule = (
        "【回答整合規則】先判斷使用者是在查哪一類內容，再選擇回答結構。\n"
        "資料查詢型（人員、電話、地址、參數、欄位、代碼）：直接列資料，不硬補背景。\n"
        "流程 / SOP 型（怎麼做、流程、步驟、操作方式）：列前置條件、步驟、注意事項、來源。\n"
        "問題 / 異常型（為什麼、原因、目前狀況、還沒解決）：列現象、原因、處理方式、狀態。\n"
        "方案 / 經驗整理型（自動化內容、架構、經驗、規劃、方案）：必須保留背景 / 原始需求、解法 / 流程、關鍵判斷、限制 / 風險與後續事項；"
        "不可只摘錄主題詞附近段落，也不可只列 action 名稱或工具選型摘要。\n\n"
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
        "【回覆格式】請使用標準 Markdown 語法輸出，回答會顯示在專屬閱讀頁（會完整渲染 Markdown）。\n"
        "- 章節用 `## 標題`、子段用 `### 子標題`；不要用「─」這類橫線字元假裝分隔。\n"
        "- 列點用 `- 項目`（破折號 + 空格）；不要用「•」字元。\n"
        "- 強調用 `**粗體**`；表格可用 GFM `| 欄 | 欄 |` 語法。\n"
        "- 欄位對照可用粗體 key：`- **狀態**：OPEN`。\n"
        "- 來源清單一律 `- [檔名](相對路徑)`，路徑用正斜線。\n"
        "  **路徑必須是你在這次回答中實際用 Read 工具讀過的檔案完整 vault 相對路徑**，"
        "不可從 index 摘要、記憶或常識猜測；不可省略或改寫資料夾名（例如 `0151-SampleProjectC-Site1` 不能簡稱為 `0151-SampleProjectC-Site1`）；"
        "不可自行更換資料夾（例如 `02_Projects/...` 不可改寫成 `04_Knowledge/...`）。"
        "若你沒有實際 Read 過該檔案，就不要把它列為來源。\n"
        "若使用者查詢 MSG、JSON、API、SQL、模板或範例，必須完整保留命中的連續程式碼/JSON 區塊，"
        "不可只摘要 EventID 或省略 Header、Body、KeepData、欄位清單；若同一檔案有多個相關模板，需逐一完整列出。\n"
        "MSG、JSON、API payload、SQL、指令或程式碼必須使用 Markdown fenced code block 包起來，"
        "例如 ```json、```sql 或 ```text，方便結果頁排版與複製。\n\n"
    )

    return (
        execution_rule
        + classification_rule
        + precheck_rule
        + query_steps
        + answer_synthesis_rule
        + write_rule
        + format_rule
        + "【使用者問題】\n"
        + prompt
    )
