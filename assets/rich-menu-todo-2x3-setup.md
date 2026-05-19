# LINE OA Manager Rich Menu 設定

圖片：`assets/rich-menu-todo-2x3-800x540.png`

尺寸：800 px x 540 px。

版型：2 排 3 欄。

## 區塊設定

| 位置 | 顯示文字 | 動作類型 | 送出文字 |
|---|---|---|---|
| 第一排左 | 查詢專案 | 文字 | 查詢專案 |
| 第一排中 | 回報問題 | 文字 | 回報問題 |
| 第一排右 | Daily Report | 文字 | 今日 Daily Report |
| 第二排左 | To-do | 文字 | To-do |
| 第二排中 | 今日待辦 | 文字 | 今日待辦 |
| 第二排右 | 全部待辦 | 文字 | 全部待辦 |

## 備註

- 程式端已支援上述六個文字入口。
- 第一版維持 LINE OA Manager 手動設定，不改 Messaging API Rich Menu。
- To-do 操作只寫 `06_System/ToDo/tasks.md`，不走 agent、不 push、不建立 LineBotResults。
