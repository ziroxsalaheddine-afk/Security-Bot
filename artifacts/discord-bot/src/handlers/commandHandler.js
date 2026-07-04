import { readdirSync } from 'fs';
import { fileURLToPath, pathToFileURL } from 'url';
import { dirname, join } from 'path';
import { Collection } from 'discord.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

export async function loadCommands(client) {
  client.commands = new Collection();

  const commandsPath = join(__dirname, '../commands');
  const files = readdirSync(commandsPath).filter(f => f.endsWith('.js'));

  for (const file of files) {
    const filePath = join(commandsPath, file);
    const { default: command } = await import(pathToFileURL(filePath).href);

    if (!command?.name) {
      console.warn(`[CMD HANDLER] Skipping ${file} — no name export.`);
      continue;
    }

    client.commands.set(command.name, command);
    console.log(`[CMD] Loaded: ${command.name}`);
  }

  console.log(`[CMD HANDLER] ${client.commands.size} commands loaded.`);
}
