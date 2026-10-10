/* SPDX-License-Identifier: ISC */
#ifndef DMRDECODE_H
#define DMRDECODE_H

#include <stddef.h>
#include <stdint.h>

#if defined(__GNUC__)
#define DMR_API __attribute__((visibility("default")))
#else
#define DMR_API
#endif

typedef struct dmr_decoder dmr_decoder;

DMR_API dmr_decoder *dmr_decoder_new(void);
DMR_API void dmr_decoder_free(dmr_decoder *decoder);
DMR_API int dmr_decoder_reset(dmr_decoder *decoder);

/* One 33-byte DMR voice burst yields 480 native-endian signed 16-bit samples.
 * errors, if provided, receives three FEC correction counts. Returns 0 on
 * success, -1 on invalid input. Calls on one instance must be serialized. */
DMR_API int dmr_decoder_decode(dmr_decoder *decoder, const uint8_t *burst,
                              size_t burst_length, int16_t *samples,
                              size_t sample_capacity, int errors[3]);

#endif
