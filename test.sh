cat > /tmp/test_nodes.sh << 'EOF'
#!/bin/bash
# 从 XQ-Network 组里拉出所有节点名,逐个测延迟
NODES=$(curl -s http://127.0.0.1:19091/proxies/XQ-Network | python3 -c "
import json,sys
d=json.load(sys.stdin)
for n in d['all']:
    print(n)
")

while IFS= read -r node; do
    # URL 编码节点名
    encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$node")
    result=$(curl -s -m 8 "http://127.0.0.1:19091/proxies/${encoded}/delay?timeout=5000&url=http://www.gstatic.com/generate_204")
    printf "%-50s %s\n" "$node" "$result"
done <<< "$NODES"
EOF
bash /tmp/test_nodes.sh