#include "../../nzportable_def.h"
#include "sdl_local.h"
#include <SDL_mixer.h>

#define SDL_DMA_SAMPLES 32768
static qboolean audio_initialized;
static unsigned int audio_position;
static unsigned char *audio_buffer;

/* Callback post-mezcla: SDL_mixer nos da su stream ya mezclado (musica) y
 * nosotros agregamos encima el ring-buffer de efectos (shm->buffer). Con esto
 * hay UN SOLO dispositivo de audio abierto (SDL 2.32 en Android solo permite
 * abrir el dispositivo por defecto una vez: "Audio device already open"). Es
 * el mismo patron que usa el port de Switch (platform/nx/snd_nx.c). */
static void SDLCALL PostMixCallback(void *userdata, Uint8 *stream, int length)
{
	int bytes = shm->samples * (shm->samplebits / 8);
	int offset = (audio_position * (shm->samplebits / 8)) % bytes;
	int first = length < bytes - offset ? length : bytes - offset;

	(void)userdata;
	SDL_MixAudioFormat(stream, shm->buffer + offset, AUDIO_S16SYS, first, SDL_MIX_MAXVOLUME);
	if (first < length)
		SDL_MixAudioFormat(stream + first, shm->buffer, AUDIO_S16SYS, length - first, SDL_MIX_MAXVOLUME);
	audio_position = (audio_position + length / (shm->samplebits / 8)) % shm->samples;
}

qboolean SNDDMA_Init(void)
{
	int frequency = 0;
	int channels = 0;

	if (SDL_InitSubSystem(SDL_INIT_AUDIO) != 0) {
		Con_Printf("SDL audio initialization failed: %s\n", SDL_GetError());
		return false;
	}
	if ((Mix_Init(MIX_INIT_MP3) & MIX_INIT_MP3) == 0) {
		Con_Printf("SDL_mixer MP3 support unavailable: %s\n", Mix_GetError());
		SDL_QuitSubSystem(SDL_INIT_AUDIO);
		return false;
	}
	if (Mix_OpenAudio(44100, AUDIO_S16SYS, 2, 1024) < 0) {
		Con_Printf("SDL_mixer audio initialization failed: %s\n", Mix_GetError());
		Mix_Quit();
		SDL_QuitSubSystem(SDL_INIT_AUDIO);
		return false;
	}

	shm = &sn;
	shm->splitbuffer = 0;
	shm->samplebits = 16;
	Mix_QuerySpec(&frequency, NULL, &channels);
	shm->speed = frequency;
	shm->channels = channels;
	shm->samples = SDL_DMA_SAMPLES;
	shm->samplepos = 0;
	shm->submission_chunk = 1;
	audio_buffer = calloc(SDL_DMA_SAMPLES, sizeof(short));
	if (!audio_buffer) {
		Mix_CloseAudio();
		Mix_Quit();
		SDL_QuitSubSystem(SDL_INIT_AUDIO);
		return false;
	}
	shm->buffer = audio_buffer;

	Mix_SetPostMix(PostMixCallback, NULL);
	audio_position = 0;
	audio_initialized = true;
	return true;
}

int SNDDMA_GetDMAPos(void)
{
	return (int)audio_position;
}

void SNDDMA_Submit(void)
{
}

void SNDDMA_Shutdown(void)
{
	if (!audio_initialized)
		return;

	Mix_SetPostMix(NULL, NULL);
	Mix_CloseAudio();
	Mix_Quit();
	SDL_QuitSubSystem(SDL_INIT_AUDIO);
	free(audio_buffer);
	audio_buffer = NULL;
	audio_initialized = false;
}
