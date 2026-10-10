/* SPDX-License-Identifier: ISC
 * AMBE interleaving coordinates follow DroidStar/mbe/mbevocoder.cpp (ISC).
 * See THIRD_PARTY_LICENSES.txt for the upstream permission notices.
 */
#include "dmrdecode.h"
#include "mbelib.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(short) == sizeof(int16_t), "mbelib requires 16-bit short");

struct dmr_decoder {
    mbe_parms current;
    mbe_parms previous;
    mbe_parms enhanced;
    uint32_t noise_state;
};

/* mbelib's unvoiced synthesis calls rand(). Route those calls through the
 * current instance so decoding another slot cannot advance its noise state.
 * The upstream source is compiled with -Drand=dmr_codec_rand. */
static _Thread_local dmr_decoder *active_decoder;

int dmr_codec_rand(void)
{
    uint32_t value = active_decoder->noise_state;
    value ^= value << 13;
    value ^= value >> 17;
    value ^= value << 5;
    active_decoder->noise_state = value;
    return (int)(value % ((uint64_t)RAND_MAX + 1));
}

static const uint8_t row_even[36] = {
    0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1,
    0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 2,
    0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2
};
static const uint8_t col_even[36] = {
    23, 10, 22, 9, 21, 8, 20, 7, 19, 6, 18, 5,
    17, 4, 16, 3, 15, 2, 14, 1, 13, 0, 12, 10,
    11, 9, 10, 8, 9, 7, 8, 6, 7, 5, 6, 4
};
static const uint8_t row_odd[36] = {
    0, 2, 0, 2, 0, 2, 0, 2, 0, 3, 0, 3,
    1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3,
    1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3
};
static const uint8_t col_odd[36] = {
    5, 3, 4, 2, 3, 1, 2, 0, 1, 13, 0, 12,
    22, 11, 21, 10, 20, 9, 19, 8, 18, 7, 17, 6,
    16, 5, 15, 4, 14, 3, 13, 2, 12, 1, 11, 0
};

dmr_decoder *dmr_decoder_new(void)
{
    dmr_decoder *decoder = calloc(1, sizeof(*decoder));
    if (decoder != NULL) {
        dmr_decoder_reset(decoder);
    }
    return decoder;
}

void dmr_decoder_free(dmr_decoder *decoder)
{
    free(decoder);
}

int dmr_decoder_reset(dmr_decoder *decoder)
{
    if (decoder == NULL) {
        return -1;
    }
    memset(decoder, 0, sizeof(*decoder));
    decoder->noise_state = 0x6d2b79f5;
    mbe_initMbeParms(&decoder->current, &decoder->previous, &decoder->enhanced);
    return 0;
}

int dmr_decoder_decode(dmr_decoder *decoder, const uint8_t *burst,
                       size_t burst_length, int16_t *samples,
                       size_t sample_capacity, int errors[3])
{
    if (decoder == NULL || burst == NULL || samples == NULL ||
        burst_length != 33 || sample_capacity < 480) {
        return -1;
    }

    uint8_t voice[27] = {0};
    /* Voice occupies bits 0..107 and 156..263; the middle 48 bits carry
     * synchronization or embedded signalling. The second AMBE frame crosses
     * that boundary, in the middle of a byte. Bits are in network order. */
    for (size_t bit = 0; bit < 216; ++bit) {
        size_t source_bit = bit < 108 ? bit : bit + 48;
        uint8_t value = (burst[source_bit / 8] >> (7 - source_bit % 8)) & 1;
        voice[bit / 8] |= value << (7 - bit % 8);
    }

    active_decoder = decoder;
    for (size_t frame = 0; frame < 3; ++frame) {
        char interleaved[4][24] = {{0}};
        char data[49] = {0};
        char error_message[64] = {0};
        int c0_errors = 0;
        int total_errors = 0;
        const uint8_t *ambe = voice + frame * 9;
        for (size_t pair = 0; pair < 36; ++pair) {
            size_t bit = pair * 2;
            interleaved[row_even[pair]][col_even[pair]] =
                (ambe[bit / 8] >> (7 - bit % 8)) & 1;
            interleaved[row_odd[pair]][col_odd[pair]] =
                (ambe[bit / 8] >> (6 - bit % 8)) & 1;
        }
        mbe_processAmbe3600x2450Frame(
            samples + frame * 160, &c0_errors, &total_errors, error_message,
            interleaved, data, &decoder->current, &decoder->previous,
            &decoder->enhanced, 3);
        if (errors != NULL) {
            errors[frame] = total_errors;
        }
    }
    active_decoder = NULL;
    return 0;
}
