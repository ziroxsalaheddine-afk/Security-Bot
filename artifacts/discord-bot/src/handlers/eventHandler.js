import { readdirSync } from 'fs';
import { fileURLToPath, pathToFileURL } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

export async function loadEvents(client) {
  const eventsPath = join(__dirname, '../events');
  const files = readdirSync(eventsPath).filter(f => f.endsWith('.js'));

  for (const file of files) {
    const filePath = join(eventsPath, file);
    const { default: event } = await import(pathToFileURL(filePath).href);

    if (!event?.name) {
      console.warn(`[EVT HANDLER] Skipping ${file} — no name export.`);
      continue;
    }

    if (event.once) {
      client.once(event.name, (...args) => event.execute(client, ...args));
    } else {
      client.on(event.name, (...args) => event.execute(client, ...args));
    }

    console.log(`[EVT] Loaded: ${event.name}`);
  }

  console.log(`[EVT HANDLER] ${files.length} events loaded.`);
}
