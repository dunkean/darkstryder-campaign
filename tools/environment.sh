# Source this file in WSL to use the project's optional portable Node installation.
darkstryder_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
darkstryder_runtime_root="$(node --input-type=module -e 'import fs from "node:fs"; import path from "node:path"; const root=process.argv[1]; const file=path.join(root,"config.local.json"); const config=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,"utf8").replace(/^\uFEFF/,"")):{}; console.log(config.runtimeRoot||path.resolve(root,"../darkstryder_runtime"));' "$darkstryder_project_root")"
darkstryder_node_bin="$darkstryder_runtime_root/tools/node/node_modules/node/bin"
if [[ -x "$darkstryder_node_bin/node" ]]; then export PATH="$darkstryder_node_bin:$PATH"; fi
unset darkstryder_project_root darkstryder_runtime_root darkstryder_node_bin
