#!/bin/bash
# TCP Bench - VPS 网络线路质量自助测试
# 用法: curl -sL https://tcpbench.com/run.sh | bash
# 说明: 纯 bash 实现，用 /dev/tcp 做 TCP 连接计时，不需要安装任何依赖。
#       测试结果会上传到 BACKEND_URL，只包含站点延迟数据和打码后的 IP。
set -o pipefail

BACKEND_URL="${BACKEND_URL:-__BACKEND_URL__}"
ROUNDS=60
TIMEOUT=5
PORT=443

NAMES=("NYTimes" "TheGuardian" "CNN" "Vercel" "Reddit" "BBC" "Azure" "Twitch" "Adobe" "TikTok" "Apple" "PayPal" "YahooMail" "Steam" "Bing" "Discord" "Zoom" "GitLab" "Twitter/X" "Udemy" "Cloudflare" "Notion" "Midjourney" "Shopify" "DeepL" "Claude" "ChatGPT" "Perplexity" "Medium" "Copilot" "Coinbase" "Microsoft" "Facebook" "Instagram" "WhatsApp" "Dropbox" "Spotify" "GoogleCloud" "Slack" "Telegram" "GitHub" "Wikipedia" "Gemini" "eBay" "Google" "Stripe" "Gmail" "Netflix" "edX" "YouTube" "AWS" "Coursera" "Trello" "Canva" "ProtonMail" "Figma" "KhanAcademy" "EpicGames" "Amazon" "Outlook" "Xbox" "Oracle" "PlayStation" "Salesforce")
HOSTS=("www.nytimes.com" "www.theguardian.com" "www.cnn.com" "vercel.com" "www.reddit.com" "www.bbc.com" "azure.microsoft.com" "www.twitch.tv" "www.adobe.com" "www.tiktok.com" "www.apple.com" "www.paypal.com" "mail.yahoo.com" "store.steampowered.com" "www.bing.com" "discord.com" "zoom.us" "gitlab.com" "twitter.com" "www.udemy.com" "www.cloudflare.com" "www.notion.so" "www.midjourney.com" "www.shopify.com" "www.deepl.com" "claude.ai" "chatgpt.com" "www.perplexity.ai" "medium.com" "copilot.microsoft.com" "www.coinbase.com" "www.microsoft.com" "www.facebook.com" "www.instagram.com" "web.whatsapp.com" "www.dropbox.com" "www.spotify.com" "cloud.google.com" "slack.com" "web.telegram.org" "github.com" "www.wikipedia.org" "gemini.google.com" "www.ebay.com" "www.google.com" "stripe.com" "mail.google.com" "www.netflix.com" "www.edx.org" "www.youtube.com" "aws.amazon.com" "www.coursera.org" "trello.com" "www.canva.com" "mail.proton.me" "www.figma.com" "www.khanacademy.org" "www.epicgames.com" "www.amazon.com" "outlook.live.com" "www.xbox.com" "www.oracle.com" "www.playstation.com" "www.salesforce.com")

TOTAL=${#NAMES[@]}

HOSTNAME_L=$(hostname)
IP=$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[ -z "$IP" ] && IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP="unknown"

tcp_ping() {
    local host=$1 port=$2 timeout=$3
    local t0 t1 elapsed
    t0=$(date +%s%N 2>/dev/null)
    [ -z "$t0" ] && { echo "null"; return; }
    if timeout "$timeout" bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then
        t1=$(date +%s%N)
        exec 3>&- 2>/dev/null || true
        elapsed=$(awk "BEGIN{printf \"%.3f\", ($t1-$t0)/1000000}")
        echo "$elapsed"
    else
        echo "null"
    fi
}

json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

echo
echo "  TCP Bench - 正在测试 $TOTAL 个站点，每个 $ROUNDS 轮..."
echo "  ────────────────────────────────────────────"

ALL_JSON=""
IDX=0
for i in "${!NAMES[@]}"; do
    NAME="${NAMES[$i]}"
    HOST="${HOSTS[$i]}"
    IDX=$((IDX+1))
    printf "  [%2d/%d] %-14s" "$IDX" "$TOTAL" "$NAME"

    SAMPLES=""
    SUCCESS=0
    SUM="0"
    MIN=""
    MAX=""
    FAIL=0

    for r in $(seq 1 $ROUNDS); do
        LAT=$(tcp_ping "$HOST" "$PORT" "$TIMEOUT")
        if [ "$LAT" = "null" ]; then
            FAIL=$((FAIL+1))
            SAMPLES="${SAMPLES}null,"
        else
            SUCCESS=$((SUCCESS+1))
            SAMPLES="${SAMPLES}${LAT},"
            SUM=$(awk "BEGIN{printf \"%.3f\", $SUM+$LAT}")
            if [ -z "$MIN" ]; then MIN=$LAT; MAX=$LAT
            else
                MIN=$(awk "BEGIN{print ($LAT<$MIN)?$LAT:$MIN}")
                MAX=$(awk "BEGIN{print ($LAT>$MAX)?$LAT:$MAX}")
            fi
        fi
        [ "$r" -lt "$ROUNDS" ] && sleep 0.08
    done

    SAMPLES="${SAMPLES%,}"

    if [ "$SUCCESS" -gt 0 ]; then
        AVG=$(awk "BEGIN{printf \"%.3f\", $SUM/$SUCCESS}")
        LOSS=$(awk "BEGIN{printf \"%.2f\", ($FAIL/$ROUNDS)*100}")
        AVG_SHOW=$(awk "BEGIN{printf \"%.2f\", $AVG}")
        printf "  %8sms\n" "$AVG_SHOW"
        ROW=$(printf '{"name":"%s","host":"%s","avg":%s,"min":%s,"max":%s,"success":%d,"total":%d,"loss_pct":%s,"samples":[%s]}' \
            "$(json_str "$NAME")" "$(json_str "$HOST")" "$AVG" "$MIN" "$MAX" "$SUCCESS" "$ROUNDS" "$LOSS" "$SAMPLES")
    else
        printf "  %8s\n" "timeout"
        ROW=$(printf '{"name":"%s","host":"%s","avg":null,"min":null,"max":null,"success":0,"total":%d,"loss_pct":100.0,"samples":[%s]}' \
            "$(json_str "$NAME")" "$(json_str "$HOST")" "$ROUNDS" "$SAMPLES")
    fi

    if [ -z "$ALL_JSON" ]; then ALL_JSON="$ROW"
    else ALL_JSON="${ALL_JSON},$ROW"
    fi
done

echo "  ────────────────────────────────────────────"
echo "  正在上传结果到 $BACKEND_URL ..."

TMP_JSON=$(mktemp)
printf '{"hostname":"%s","ip":"%s","results":[%s]}\n' \
    "$(json_str "$HOSTNAME_L")" "$(json_str "$IP")" "$ALL_JSON" > "$TMP_JSON"

RESP=$(curl -sS -X POST "$BACKEND_URL/api/report" \
    -H "Content-Type: application/json" \
    --data-binary "@$TMP_JSON")
CURL_STATUS=$?
rm -f "$TMP_JSON"

REPORT_URL=$(echo "$RESP" | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ "$CURL_STATUS" -eq 0 ] && [ -n "$REPORT_URL" ]; then
    echo
    echo "  ✓ 测试完成，报告链接："
    echo "  $REPORT_URL"
    echo
else
    echo
    echo "  ✗ 上传失败：$RESP"
    echo
fi
