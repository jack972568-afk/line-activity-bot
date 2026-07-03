import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# 從雲端環境變數讀取金鑰
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# 初始化最新版的 Google GenAI 用戶端
client = genai.Client(api_key=GEMINI_KEY)

# 全面升級：整合官方 Custom Search API 與針對性網頁微調
def search_and_scrape(query):
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    cx = os.environ.get("SEARCH_ENGINE_ID")
    
    if not api_key or not cx:
        return "系統設定錯誤：缺少 Google 搜尋 API 金鑰或搜尋引擎 ID。"

    # 呼叫官方 API（設定 num=5 抓取前五筆結果）
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={api_key}&cx={cx}&num=5"
    
    try:
        res = requests.get(search_url, timeout=10)
        data = res.json()
        
        if 'items' not in data:
            # 強制印出 Google 官方的原始回傳數據，抓出真正的錯誤原因
            return f"系統除錯警報！Google API 未正常回傳資料。原始數據為：{str(data)}"
            
        combined_text = ""
        # 微調：模擬真實瀏覽器以降低基礎反爬蟲阻擋機率
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
                
                # 針對活動網站微調：優先抓取 SEO 用的 Meta Description 與 OG Description
                meta_desc = page_soup.find('meta', attrs={'name': 'description'})
                og_desc = page_soup.find('meta', attrs={'property': 'og:description'})
                
                if og_desc and og_desc.get('content'):
                    page_text += f" [社群摘要: {og_desc.get('content')}] "
                elif meta_desc and meta_desc.get('content'):
                    page_text += f" [網頁摘要: {meta_desc.get('content')}] "
                
                # 備用機制：抓取前幾個 HTML 段落文字
                paragraphs = page_soup.find_all('p')
                fallback_text = " ".join([p.get_text().strip() for p in paragraphs[:3] if p.get_text().strip()])
                page_text += f" [內文: {fallback_text}]"
                
            except:
                page_text = "無法讀取此網頁完整內文（該目標網站設有強烈反爬蟲防護或為動態網頁）。"
                
            # 將所有維度的資訊組合，提供給大腦分析
            combined_text += f"\n[標題: {title}]\n[來源網頁: {link}]\n[Google官方摘要: {snippet}]\n[網頁擷取內文: {page_text[:400]}]\n"
            
        return combined_text
        
    except Exception as e:
        return f"搜尋系統發生錯誤: {str(e)}"

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
    
    # 提醒用戶系統正在透過官方 API 抓取資料
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕵️‍♂️ 正在為您連線官方 API 搜查即時情報，請稍候約 20-30 秒..."))
    
    search_raw_data = search_and_scrape(user_text)
    
    # 嚴格的 Prompt 設定，確保輸出符合您的理性與格式要求
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
    
    try:
        # 使用最新真實支援的 Gemini 3.5 Flash 模型
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt,
        )
        ai_result = response.text
    except Exception as e:
        ai_result = f"AI 分析失敗，原因: {str(e)}"
        
    line_bot_api.push_message(event.source.user_id, TextSendMessage(text=ai_result))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
