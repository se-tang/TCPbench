#!/bin/bash
# TCPBench v2 - fixed-IP, family-separated TCP handshake benchmark.
# Usage: curl -sL https://your-domain.com/run.sh | bash
# Optional: IP_MODE=v4|v6|both curl -sL https://your-domain.com/run.sh | bash
set -o pipefail
export LC_ALL=C

BACKEND_URL="${BACKEND_URL:-__BACKEND_URL__}"
ROUNDS=60
TIMEOUT=3
PRESELECT_TIMEOUT=2
PORT=443
PRESELECT_ROUNDS=3
MAX_CANDIDATES=4

NAMES=("NYTimes" "TheGuardian" "CNN" "Vercel" "Reddit" "BBC" "Azure" "Adobe" "TikTok" "Apple" "PayPal" "YahooMail" "Steam" "Discord" "Zoom" "GitLab" "Twitter/X" "Udemy" "Cloudflare" "Notion" "Midjourney" "Shopify" "DeepL" "Claude" "ChatGPT" "Perplexity" "Medium" "Copilot" "Coinbase" "Microsoft" "Facebook" "Instagram" "WhatsApp" "Dropbox" "Spotify" "GoogleCloud" "Slack" "Telegram" "GitHub" "Wikipedia" "Gemini" "eBay" "Google" "Stripe" "Gmail" "Netflix" "edX" "YouTube" "AWS" "Coursera" "Trello" "Canva" "ProtonMail" "Figma" "KhanAcademy" "EpicGames" "Amazon" "Outlook" "Xbox" "Oracle" "PlayStation" "Salesforce" "Twitch" "Grok" "DeepSeek" "HuggingFace" "Poe" "Signal" "Line" "Skype" "Teams" "DigitalOcean" "Vultr" "Hetzner" "Linode" "Netlify")
HOSTS=("www.nytimes.com" "www.theguardian.com" "www.cnn.com" "vercel.com" "www.reddit.com" "www.bbc.com" "azure.microsoft.com" "www.adobe.com" "www.tiktok.com" "www.apple.com" "www.paypal.com" "mail.yahoo.com" "store.steampowered.com" "discord.com" "zoom.us" "gitlab.com" "twitter.com" "www.udemy.com" "www.cloudflare.com" "www.notion.so" "www.midjourney.com" "www.shopify.com" "www.deepl.com" "claude.ai" "chatgpt.com" "www.perplexity.ai" "medium.com" "copilot.microsoft.com" "www.coinbase.com" "www.microsoft.com" "www.facebook.com" "www.instagram.com" "web.whatsapp.com" "www.dropbox.com" "www.spotify.com" "cloud.google.com" "slack.com" "web.telegram.org" "github.com" "www.wikipedia.org" "gemini.google.com" "www.ebay.com" "www.google.com" "stripe.com" "mail.google.com" "www.netflix.com" "www.edx.org" "www.youtube.com" "aws.amazon.com" "www.coursera.org" "trello.com" "www.canva.com" "mail.proton.me" "www.figma.com" "www.khanacademy.org" "www.epicgames.com" "www.amazon.com" "outlook.live.com" "www.xbox.com" "www.oracle.com" "www.playstation.com" "www.salesforce.com" "www.twitch.tv" "grok.com" "chat.deepseek.com" "huggingface.co" "poe.com" "signal.org" "line.me" "skype.com" "teams.microsoft.com" "digitalocean.com" "vultr.com" "hetzner.com" "linode.com" "netlify.com")
V6_SKIP_NAMES=("NYTimes" "Vercel" "Reddit" "BBC" "TikTok" "PayPal" "Steam" "Discord" "Twitter/X" "Shopify" "Slack" "GitHub" "eBay" "Stripe" "Coursera" "Trello" "ProtonMail" "Figma" "KhanAcademy" "EpicGames" "Twitch" "DeepSeek" "Poe" "Line")

declare -A V6_SKIP=()
for _skip_name in "${V6_SKIP_NAMES[@]}"; do
    V6_SKIP["$_skip_name"]=1
done
unset _skip_name

TOTAL=${#NAMES[@]}
HOSTNAME_L=$(hostname)
DNS_SERVER=$(awk '/^nameserver[[:space:]]+/{print $2}' /etc/resolv.conf 2>/dev/null | paste -sd, -)

json_str() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

now_us() {
    local value=${EPOCHREALTIME:-}
    if [ -n "$value" ]; then
        local whole=${value%.*}
        local frac=${value#*.}000000
        printf '%s' "$((10#$whole * 1000000 + 10#${frac:0:6}))"
        return
    fi
    date +%s%6N
}

duration_ms() {
    local elapsed=$(( $2 - $1 ))
    printf '%d.%03d' "$((elapsed / 1000))" "$((elapsed % 1000))"
}

tcp_ping() {
    local target=$1
    local limit=${2:-$TIMEOUT}
    local started ended
    started=$(now_us)
    if timeout "$limit" bash -c "exec 3<>/dev/tcp/$target/$PORT" 2>/dev/null; then
        ended=$(now_us)
        duration_ms "$started" "$ended"
    else
        printf 'null'
    fi
}

resolve_family() {
    local host=$1
    local family=$2
    local attempt started ended raw found all="" total_dns=0 used_attempts=0

    for attempt in 1 2 3; do
        used_attempts=$((used_attempts + 1))
        started=$(now_us)
        if [ "$family" = "v4" ]; then
            raw=$(getent ahostsv4 "$host" 2>/dev/null)
            found=$(printf '%s\n' "$raw" | awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ {print $1}')
        else
            raw=$(getent ahostsv6 "$host" 2>/dev/null)
            found=$(printf '%s\n' "$raw" | awk '$1 ~ /^[0-9a-fA-F:]+$/ && $1 !~ /^::ffff:/ {sub(/%25.*/, "", $1); sub(/%[^:]*/, "", $1); print $1}')
        fi
        ended=$(now_us)
        total_dns=$((total_dns + ended - started))
        if [ -n "$found" ]; then
            all="${all}${found}"$'\n'
        elif [ -z "$all" ]; then
            # 地址族没有首条记录时不必重复查同一个稳定域名，避免无谓等待。
            break
        fi
    done

    RESOLVED_IPS=$(printf '%s' "$all" | awk 'NF && !seen[$0]++' | head -n "$MAX_CANDIDATES")
    DNS_MS=$(duration_ms 0 "$((total_dns / used_attempts))")
    CANDIDATE_COUNT=$(printf '%s\n' "$RESOLVED_IPS" | awk 'NF {count++} END {print count+0}')
}

detect_ipv6() {
    local ip
    resolve_family "google.com" "v6"
    for ip in $RESOLVED_IPS; do
        if [ "$(tcp_ping "$ip")" != "null" ]; then
            return 0
        fi
    done
    return 1
}

choose_mode() {
    local choice
    if ! detect_ipv6; then
        IP_MODE="v4"
        return
    fi

    if [ -n "${IP_MODE:-}" ]; then
        case "$IP_MODE" in
            v4|v6|both) return ;;
            *) IP_MODE="v4"; return ;;
        esac
    fi

    if [ -r /dev/tty ]; then
        printf '\033[2J\033[H' > /dev/tty
        printf '\n  TCPBench\n' > /dev/tty
        printf '  ─────────────────────────────\n' > /dev/tty
        printf '  1  仅测 IPv4\n' > /dev/tty
        printf '  2  仅测 IPv6\n' > /dev/tty
        printf '  3  两者都测\n\n' > /dev/tty
        while true; do
            printf '  请选择 [默认 3]: ' > /dev/tty
            IFS= read -r choice < /dev/tty || choice=""
            case "$choice" in
                ""|3) IP_MODE="both"; return ;;
                1) IP_MODE="v4"; return ;;
                2) IP_MODE="v6"; return ;;
                *) printf '  请输入 1、2 或 3。\n' > /dev/tty ;;
            esac
        done
    fi

    IP_MODE="v4"
}

draw_progress_bar() {
    local current=$1
    local total=$2
    local width=24
    local percent filled empty bar blank
    [ "$total" -le 0 ] && return
    percent=$((current * 100 / total))
    filled=$((current * width / total))
    [ "$filled" -gt "$width" ] && filled=$width
    printf -v blank '%*s' "$filled" ''
    bar=${blank// /#}
    printf -v blank '%*s' "$((width - filled))" ''
    empty=${blank// /-}

    if [ -t 1 ]; then
        printf '\r  [%s%s] %3d%% (%d/%d)' "$bar" "$empty" "$percent" "$current" "$total"
        [ "$current" -eq "$total" ] && printf '\n'
    elif [ "$current" -eq "$total" ] || [ $((current % 10)) -eq 0 ]; then
        printf '  预检进度 %3d%% (%d/%d)\n' "$percent" "$current" "$total"
    fi
}

get_primary_ip() {
    local family=$1
    local ip=""
    if [ "$family" = "v4" ]; then
        ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
    else
        ip=$(ip -6 route get 2606:4700:4700::1111 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
    fi
    [ -z "$ip" ] && ip=$(hostname -I 2>/dev/null | tr ' ' '\n' | awk -v family="$family" '
        family == "v4" && /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ {print; exit}
        family == "v6" && /:/ && !/^::ffff:/ {print; exit}
    ')
    [ -z "$ip" ] && ip="unknown"
    printf '%s' "$ip"
}

median_from_file() {
    sort -n "$1" | awk '{a[NR]=$1} END {
        if (!NR) exit 1
        if (NR % 2) printf "%.3f", a[(NR+1)/2]
        else printf "%.3f", (a[NR/2] + a[NR/2+1]) / 2
    }'
}

select_target() {
    local candidate values value median best="" best_median=""
    PRECHECK_OK=0
    : > "$PRESELECT_FILE"
    for candidate in $RESOLVED_IPS; do
        values=""
        for _ in $(seq 1 "$PRESELECT_ROUNDS"); do
            value=$(tcp_ping "$candidate" "$PRESELECT_TIMEOUT")
            if [ "$value" != "null" ]; then
                values="${values}${value}"$'\n'
            fi
        done
        if [ -z "$values" ]; then
            continue
        fi
        printf '%s' "$values" > "$PRESELECT_FILE"
        median=$(median_from_file "$PRESELECT_FILE")
        if [ -z "$best_median" ] || awk -v a="$median" -v b="$best_median" 'BEGIN {exit !(a < b)}'; then
            best="$candidate"
            best_median="$median"
        fi
    done
    if [ -z "$best" ]; then
        best=$(printf '%s\n' "$RESOLVED_IPS" | awk 'NF {print; exit}')
    else
        PRECHECK_OK=1
    fi
    TARGET_IP=$best
}

prepare_family() {
    local family=$1
    local i name resolve_total processed=0 family_label
    TARGET_IPS=()
    SUPPORTED=()
    SITE_DNS=()
    CANDIDATES=()
    PRECHECK=()
    TEST_INDICES=()

    resolve_total=$TOTAL
    if [ "$family" = "v6" ]; then
        resolve_total=$((TOTAL - ${#V6_SKIP_NAMES[@]}))
        family_label="IPv6"
    else
        family_label="IPv4"
    fi
    printf '  %s · 正在解析并预检候选 IP...\n' "$family_label"
    for i in "${!NAMES[@]}"; do
        name=${NAMES[$i]}
        if [ "$family" = "v6" ] && [ "${V6_SKIP[$name]:-0}" -eq 1 ]; then
            continue
        fi
        processed=$((processed + 1))
        resolve_family "${HOSTS[$i]}" "$family"
        SITE_DNS[$i]=$DNS_MS
        CANDIDATES[$i]=$CANDIDATE_COUNT
        if [ "$CANDIDATE_COUNT" -eq 0 ]; then
            SUPPORTED[$i]=0
            PRECHECK[$i]=0
            TARGET_IPS[$i]=""
            draw_progress_bar "$processed" "$resolve_total"
            continue
        fi
        select_target
        SUPPORTED[$i]=1
        PRECHECK[$i]=$PRECHECK_OK
        TARGET_IPS[$i]=$TARGET_IP
        draw_progress_bar "$processed" "$resolve_total"
        TEST_INDICES+=("$i")
    done
    FAMILY_TOTAL=${#TEST_INDICES[@]}
}

run_family_tests() {
    local family=$1
    local site_round i latency sample_count missing padding progress_index=0
    local consecutive valid_file p50
    local -a samples success

    prepare_family "$family"
    for i in "${TEST_INDICES[@]}"; do
        samples[$i]=""
        success[$i]=0
    done

    valid_file=$(mktemp)
    for i in "${TEST_INDICES[@]}"; do
        progress_index=$((progress_index + 1))
        name=${NAMES[$i]}
        if [ "${SUPPORTED[$i]}" -eq 0 ]; then
            printf '  [%2d/%d] %-16s  不支持\n' "$progress_index" "$FAMILY_TOTAL" "$name"
            continue
        fi
        if [ "${PRECHECK[$i]}" -eq 0 ]; then
            samples[$i]=$(printf 'null,%.0s' $(seq 1 60))
            samples[$i]="${samples[$i]%,}"
            printf '  [%2d/%d] %-16s  timeout\n' "$progress_index" "$FAMILY_TOTAL" "$name"
            continue
        fi

        # 预热连接丢弃，避免首个连接的 ARP / 路由 / TCP 缓存冷启动污染 P50。
        tcp_ping "${TARGET_IPS[$i]}" >/dev/null
        consecutive=0
        for site_round in $(seq 1 60); do
            latency=$(tcp_ping "${TARGET_IPS[$i]}")
            samples[$i]="${samples[$i]}${latency},"
            if [ "$latency" != "null" ]; then
                success[$i]=$((success[$i] + 1))
                consecutive=0
            else
                consecutive=$((consecutive + 1))
            fi
            if [ "$consecutive" -ge 3 ]; then
                missing=$((60 - site_round))
                if [ "$missing" -gt 0 ]; then
                    padding=$(printf 'null,%.0s' $(seq 1 "$missing"))
                    samples[$i]="${samples[$i]}${padding%,}"
                fi
                break
            fi
            sleep 0.08
        done

        printf '%s\n' "${samples[$i]%,}" | tr ',' '\n' | awk '$1 != "null" && $1 != "" {print}' > "$valid_file"
        if [ -s "$valid_file" ]; then
            p50=$(median_from_file "$valid_file")
            printf '  [%2d/%d] %-16s %8sms\n' \
                "$progress_index" "$FAMILY_TOTAL" "$name" "$p50"
        else
            printf '  [%2d/%d] %-16s  timeout\n' \
                "$progress_index" "$FAMILY_TOTAL" "$name"
        fi
    done
    rm -f "$valid_file"

    RESULTS_JSON=""
    for i in "${TEST_INDICES[@]}"; do
        local name=${NAMES[$i]} host=${HOSTS[$i]} target=${TARGET_IPS[$i]}
        local dns=${SITE_DNS[$i]} candidates=${CANDIDATES[$i]}
        local sample_csv="${samples[$i]%,}" supported=true
        local valid_file avg p50 min max loss row
        valid_file=$(mktemp)
        if [ "${SUPPORTED[$i]}" -eq 0 ]; then
            supported=false
            row=$(printf '{"name":"%s","host":"%s","target_ip":null,"dns_ms":%s,"supported":false,"candidate_count":%d,"avg":null,"min":null,"max":null,"p50":null,"success":0,"total":0,"loss_pct":0,"samples":[]}' \
                "$(json_str "$name")" "$(json_str "$host")" "$dns" "$candidates")
        else
            sample_count=0
            if [ -n "${samples[$i]}" ]; then
                sample_count=$(printf '%s' "${samples[$i]}" | awk -F, '{for (i=1; i<=NF; i++) if ($i != "") count++} END {print count+0}')
            fi
            if [ "$sample_count" -lt 60 ]; then
                missing=$((60 - sample_count))
                padding=$(printf 'null,%.0s' $(seq 1 "$missing"))
                if [ "$sample_count" -eq 0 ]; then
                    samples[$i]="${padding%,}"
                else
                    samples[$i]="${samples[$i]}${padding%,}"
                fi
                sample_csv="${samples[$i]%,}"
            fi
            printf '%s\n' "$sample_csv" | tr ',' '\n' | awk '$1 != "null" && $1 != "" {print}' > "$valid_file"
            if [ -s "$valid_file" ]; then
                avg=$(awk '{sum += $1} END {printf "%.3f", sum / NR}' "$valid_file")
                p50=$(median_from_file "$valid_file")
                min=$(sort -n "$valid_file" | head -1)
                max=$(sort -n "$valid_file" | tail -1)
                loss=$(awk -v success="${success[$i]}" 'BEGIN {printf "%.2f", ((60-success)/60)*100}')
                row=$(printf '{"name":"%s","host":"%s","target_ip":"%s","dns_ms":%s,"supported":true,"candidate_count":%d,"avg":%s,"min":%s,"max":%s,"p50":%s,"success":%d,"total":60,"loss_pct":%s,"samples":[%s]}' \
                    "$(json_str "$name")" "$(json_str "$host")" "$(json_str "$target")" "$dns" "$candidates" "$avg" "$min" "$max" "$p50" "${success[$i]}" "$loss" "$sample_csv")
            else
                row=$(printf '{"name":"%s","host":"%s","target_ip":"%s","dns_ms":%s,"supported":true,"candidate_count":%d,"avg":null,"min":null,"max":null,"p50":null,"success":0,"total":60,"loss_pct":100.00,"samples":[%s]}' \
                    "$(json_str "$name")" "$(json_str "$host")" "$(json_str "$target")" "$dns" "$candidates" "$sample_csv")
            fi
        fi
        rm -f "$valid_file"
        if [ -z "$RESULTS_JSON" ]; then RESULTS_JSON="$row"; else RESULTS_JSON="${RESULTS_JSON},${row}"; fi
    done
}

upload_family() {
    local family=$1 ip=$2 suffix="" tmp response body code url today total batch token
    local family_label
    [ "$family" = "v4" ] && family_label="IPv4" || family_label="IPv6"

    if [ -n "$BATCH_ID" ]; then
        suffix=$(printf ',"batch_id":"%s","owner_token":"%s"' "$(json_str "$BATCH_ID")" "$(json_str "$OWNER_TOKEN")")
    fi
    tmp=$(mktemp)
    printf '{"hostname":"%s","ip":"%s","measurement_version":2,"ip_family":"%s","dns_server":"%s"%s,"results":[%s]}\n' \
        "$(json_str "$HOSTNAME_L")" "$(json_str "$ip")" "$family" "$(json_str "$DNS_SERVER")" "$suffix" "$RESULTS_JSON" > "$tmp"

    printf '  正在上传 %s 结果到 %s ...\n' "$family_label" "$BACKEND_URL"
    response=$(curl -sS -X POST "$BACKEND_URL/api/report" \
        -H "Content-Type: application/json" \
        --data-binary "@$tmp" \
        -w $'\n%{http_code}')
    rm -f "$tmp"
    code=${response##*$'\n'}
    body=${response%$'\n'*}

    url=$(printf '%s' "$body" | sed -n 's/.*"url":"\([^"]*\)".*/\1/p' | head -1)
    today=$(printf '%s' "$body" | sed -n 's/.*"today_count":\([0-9]*\).*/\1/p' | head -1)
    total=$(printf '%s' "$body" | sed -n 's/.*"total_count":\([0-9]*\).*/\1/p' | head -1)
    batch=$(printf '%s' "$body" | sed -n 's/.*"batch_id":"\([^"]*\)".*/\1/p' | head -1)
    token=$(printf '%s' "$body" | sed -n 's/.*"owner_token":"\([^"]*\)".*/\1/p' | head -1)

    if [ "$code" = "200" ] && [ -n "$url" ]; then
        [ -n "$today" ] && [ -n "$total" ] && printf '  今日已运行 %s 次，累计运行 %s 次\n' "$today" "$total"
        printf '  ✓ %s 测试完成，测试者专用链接：\n  %s\n\n' "$family_label" "$url"
        if [ -n "$batch" ] && [ -n "$token" ]; then
            BATCH_ID=$batch
            OWNER_TOKEN=$token
        fi
        return 0
    fi

    printf '  ✗ %s 上传失败（HTTP %s）：%s\n\n' "$family_label" "$code" "$body"
    return 1
}

run_one_family() {
    local family=$1
    local ip
    ip=$(get_primary_ip "$family")
    run_family_tests "$family"
    upload_family "$family" "$ip"
}

PRESELECT_FILE=$(mktemp)
trap 'rm -f "$PRESELECT_FILE"' EXIT

choose_mode

case "$IP_MODE" in
    v4)
        run_one_family v4 || exit 1
        ;;
    v6)
        run_one_family v6 || exit 1
        ;;
    both)
        run_one_family v4 || exit 1
        if ! run_one_family v6; then
            printf '  IPv6 结果未完成；上面的 IPv4 链接仍然有效。\n\n'
            exit 1
        fi
        ;;
esac
