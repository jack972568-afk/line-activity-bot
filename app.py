import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai

app = Flask(__name__)

# 從雲端環境變數讀取金鑰
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)
genai.configure(api_key=GEMINI_KEY)

# 簡單的網頁搜尋與文字抓取函數
def search_and_scrape(query):
    # 使用 Google 免費的 html 搜尋入口（輕量化簡單實作）
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    search_url = f"https://www.google.com/search?q={query}"
    
    try:
        res = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 抓取搜尋結果的前 5 個網頁連結
        links = []
        for g in soup.find_all('div', class_='g'):
            anchors = g.find_all('a')
            if anchors:
                link = anchors[0]['href']
                if link.startswith('http'):
                    links.append(link)
            if len(links) >= 5:
                break
                
        # 爬取這 5 個網頁的內文摘要
        combined_text = ""
        for link in links:
            try:
                page_res = requests.get(link, headers=headers, timeout=5)
                page_soup = BeautifulSoup(page_res.text, 'html.parser')
                # 僅抓取網頁段落文字，限制字數避免 Token 爆炸
                paragraphs = page_soup.find_all('p')
                text = " ".join([p.get_text() for p in paragraphs[:5]])
                combined_text += f"\n[來源網頁: {link}]\n{text}\n"
            except:
                continue
        return combined_text if combined_text else "搜尋未找到有效網頁內容。"
    except Exception as e:
        return f"搜尋出錯: {str(e)}"

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
    
    # 告訴用戶系統正在處理
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🕵️‍♂️ 正在為您搜查即時情報，請稍候約 20-30 秒..."))
    
    # 1. 執行搜尋
    search_raw_data = search_and_scrape(user_text)
    
    # 2. 呼叫 Gemini 進行結構化分析
    # 這裡使用雙模型邏輯的提示詞：引導模型做客觀精確的摘要
    model = genai.GenerativeModel('gemini-1.5-flash') # 免費額度充足且速度快的模型
    
    prompt = f"""
    你是一個嚴謹的活動情報分析助理。請閱讀以下由網路搜集到的原始網頁內容，針對用戶查詢的指令「{user_text}」進行客觀分析。
    
    原始網頁內容：
    \"\"\"
    {search_raw_data}
    \"\"\"
    
    請遵守以下規則：
    1. 必須基於上方提供的真實文字回答，嚴禁虛構活動。若未提及年份，請結合上下文判斷是否為2026年或最新的活動，過期活動請忽略。
    2. 請先給出一個「即時總覽報告」（使用條列式，說明有哪些活動、時間、地點、優惠贈品亮點）。
    3. 在總覽下方，針對每個活動提供「詳細資料」區塊，包含：活動名稱、參加條件/門檻、詳細時間地點、活動內容細節、以及有無領取贈品、抽獎、優惠。
    4. 為了適合手機閱讀，請使用簡潔流暢的中文段落與 Bullet points 呈現，不要製造任何 Markdown 表格。
    """
    
    try:
        response = model.generate_content(prompt)
        ai_result = response.text
    except Exception as e:
        ai_result = f"AI 分析失敗，原因: {str(e)}"
        
    # 3. 將結果主動推播回用戶（因為 reply_token 在前面已經用掉，此處使用 push_message）
    line_bot_api.push_message(event.source.user_id, TextSendMessage(text=ai_result))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
