import path from "path";
import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);

// composition.json guarda caminhos relativos à raiz do projeto (não a
// remotion/), porque quem os produz é o pipeline Python em cache/ e output/
// fora desta pasta. Apontar o public dir pra raiz permite usar staticFile()
// com esses mesmos caminhos relativos, sem duplicar/copiar os arquivos.
// __dirname aqui NÃO aponta para remotion/ (o config é executado pelo próprio
// CLI a partir de node_modules/@remotion/cli, não do arquivo fonte) — por
// isso usamos process.cwd(), que é remotion/ quando o comando é rodado de lá
// (é como pipeline.py invoca o subprocess: cwd=remotion_dir).
Config.setPublicDir(path.resolve(process.cwd(), ".."));
