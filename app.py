import os
import time
import requests
import threading
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# 從雲端環境變數讀取金鑰，並使用 strip 清除可能的隱形空白
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# 安全檢查：避免變數遺失導致伺服器直接崩潰
if not LINE_ACCESS_TOKEN or not LINE_SECRET:
    print("嚴重警告：LINE 金鑰未正確設定，機器人將無法運作。")

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# 安全初始化 Google GenAI 用戶端
client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

def search_and_scrape(query):
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "").strip()
    cx = os.environ.get("SEARCH_ENGINE_ID", "").strip()
    
    if not api_key or not cx:
        return "系統設定錯誤：缺少 Google 搜尋 API 金鑰或搜尋引擎 ID。"

    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "q": query,
        "key": api_key,
        "cx": cx,
        "num": 5
    }
    
    try:
        res = requests.get(search_url, params=params, timeout=10)
        data = res.json()
        
        # 顯示真實的錯誤代碼，避免瞎子摸象
        if 'items' not in data:
            return f"系統除錯警報！Google API 未正常回傳資料。原始數據為：{str(data)}"
            
        combined_text = ""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        
        for item in data['items']:
            link = item.get('link')
            title = item.get('title', '無標題')
            snippet = item.get('snippet', '無摘要')
            
            page_text = ""
            try:
                page_res = requests.get(link, headers=headers, timeout=5)
                page_res.encoding = 'utf-8'
                page_soup = BeautifulSoup(page_res.text, 'html.parser')
                
                meta_desc = page_soup.find('meta', attrs={'name': 'description'})
                og_desc = page_soup.find('meta', attrs={'property': 'og:description'})
                
                if og_desc and og_desc.get('content'):
                    page_text += f" [社群摘要: {og_desc.get('content')}] "
                elif meta_desc and meta_desc.get('content'):
                    page_text += f" [網頁摘要: {meta_desc.get('content')}] "
                
                paragraphs = page_soup.find_all('p')
                fallback_text = " ".join([p.get_text().strip() for p in paragraphs[:3] if p.get_text().strip()])
                page_text += f" [內文: {fallback_text}]"
                
            except:
                page_text = "無法讀取此網頁完整內文（該目標網站設有強烈反爬蟲防護或為動態網頁）。"
                
            combined_text += f"\n[標題: {title}]\n[來源網頁: {link}]\n[Google官方摘要: {snippet}]\n[網頁擷取內文: {page_text[:400]}]\n"
            
        return combined_text
        
    except Exception as e:
        return f"搜尋系統發生錯誤: {str(e)}"

# 將耗時的 AI 分析與推送任務獨立封裝，供背景執行緒呼叫
def process_and_push_result(user_id, user_text):
    if not client:
        line_bot_api.push_message(user_id, TextSendMessage(text="系統設定錯誤：Gemini API 金鑰遺失，無法進行分析。"))
        return

    search_raw_data = search_and_scrape(user_text)
    
    prompt = f"""
    你是一個嚴謹的活動情報分析助理。請閱讀以下由 Google Custom Search API 搜集到的官方摘要與網頁擷取內容，針對用戶查詢的指令「{user_text}」進行客觀分析。
    
    原始網頁內容：
    \"\"\"
    {search_raw_data}
    \"\"\"
    
    請遵守以下最高指導原則：
    1. 必須基於上方提供的真實文字回答，嚴禁虛構或推想活動。若資訊不清楚、搜尋不到或活動已過期，必須誠實直言相告。
    2. 請先給出一個「即時總覽報告」（說明有哪些活動、時間、地點、優惠贈品亮點）。
    3. 在總覽下方，針對每個活動提供「詳細資料」區塊，包含：活動名稱、參加條件/門檻、詳細時間地點、活動內容細節、以及有無領取贈品、抽獎、優惠。
    4. 回覆內容僅能使用條列式（Bullet points）或流暢的段落文字呈現。絕對不需要結構化成表格，嚴禁製作任何形式的 Markdown 表格或圖表。
    """
    
    # --- 自動退避重試機制開始 ---
    max_retries = 3
    retry_delay = 2
    ai_result = "AI 分析失敗，已達到最大重試次數。" 
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
            )
            ai_result = response.text
            break 
            
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    ai_result = f"AI 伺服器目前極度壅塞，已自動重試 {max_retries} 次仍無法連線，請稍後再試。"
            else:
                ai_result = f"AI 分析發生未預期的系統錯誤: {error_msg}"
                break
    # --- 自動退避重試機制結束 ---
    
    # 將最終結果推播給使用者
    line_bot_api.push_message(user_id, TextSendMessage(text=ai_result))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    user_id = event.source.user_id
    
    # 第一步：迅速消耗 reply_token 安撫用戶，並在 2 秒內釋放主線程以滿足 LINE Webhook 規定
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕵️‍♂️ 正在為您連線官方 API 搜查即時情報，請稍候約 20-30 秒..."))
    
    # 第二步：啟動背景執行緒，將漫長的搜尋與 AI 生成任務丟到後台處理
    thread = threading.Thread(target=process_and_push_result, args=(user_id, user_text))
    thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
