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
//
// modules/renderer.py monta uma pasta de staging só com os arquivos deste
// render (áudio + footage referenciados) e passa o caminho via
// REMOTION_PUBLIC_DIR — evita copiar cache/ e output/ inteiros (todo footage
// e vídeo já gerados em qualquer execução) a cada render. Sem essa variável
// (ex: `npx remotion studio` rodado manualmente), cai de volta pra raiz do
// projeto inteira.
const publicDir = process.env.REMOTION_PUBLIC_DIR
  ? path.resolve(process.env.REMOTION_PUBLIC_DIR)
  : path.resolve(process.cwd(), "..");
Config.setPublicDir(publicDir);
