"""Bounded fixed-pallet registration and background-only illumination correction.

All processing uses ROI copies; no sensor settings, policy inputs or references
are changed. Textured background must agree before extending the color limit.
"""
import cv2
import numpy as np


def align(current, reference, maximum):
    height, width = reference.shape[:2]
    valid = np.ones((height, width), np.uint8)
    result = dict(alignment_shift=[0, 0], alignment_score=None, alignment_applied=False)
    if maximum == 0 or min(height, width) < maximum * 2 + 12:
        return reference, valid, result
    a = cv2.cvtColor(current, cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY)
    # Additive light changes cancel in gradients. The search cannot follow an
    # object around the pallet: it is bounded to a few ROI pixels.
    def edges(image):
        image = image.astype(np.float32)
        return cv2.magnitude(cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3),
                             cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3))
    template = edges(b)[maximum:-maximum, maximum:-maximum]
    if float(template.std()) < 3:
        return reference, valid, result
    scores = cv2.matchTemplate(edges(a), template, cv2.TM_CCOEFF_NORMED)
    _, score, _, point = cv2.minMaxLoc(scores)
    result['alignment_score'] = float(score)
    if not np.isfinite(score) or score < .45:
        return reference, valid, result
    dx, dy = point[0] - maximum, point[1] - maximum
    if dx or dy:
        transform = np.float32([[1, 0, dx], [0, 1, dy]])
        reference = cv2.warpAffine(reference, transform, (width, height), flags=cv2.INTER_NEAREST,
                                   borderMode=cv2.BORDER_REPLICATE)
        valid = cv2.warpAffine(valid, transform, (width, height), flags=cv2.INTER_NEAREST,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        result.update(alignment_shift=[dx, dy], alignment_applied=True)
    return reference, valid, result


def background_light(a, b, lo, hi, valid, limit):
    height, width = valid.shape
    tiles, textured = [], 0
    step = 12
    for y in range(0, height - step + 1, step):
        for x in range(0, width - step + 1, step):
            section = np.s_[y:y+step, x:x+step]
            if not valid[section].all():
                continue
            reference = b[section]
            luminance = reference.mean(axis=2)
            if np.percentile(luminance, 90) - np.percentile(luminance, 10) < 10:
                continue
            textured += 1
            offset = np.median(a[section] - reference, axis=(0, 1))
            corrected = a[section] - offset
            residual = np.max(np.maximum(lo[section] - corrected, corrected - hi[section]), axis=2)
            # A flat product covering a textured lattice fails this test even
            # if its average color could be explained by a brightness change.
            if np.mean(residual > 8) > .06 or np.mean(residual > 20) > .02:
                continue
            tiles.append((x + step/2, y + step/2, offset))
    metadata = dict(lighting_anchor_tiles=0, lighting_textured_tiles=textured,
                    lighting_consensus=0., lighting_coverage=[0., 0.])
    if len(tiles) < 6:
        return None, dict(metadata, lighting_reason='배경 격자 근거 부족')
    offsets = np.array([tile[2] for tile in tiles])
    distance = np.max(np.abs(offsets[:, None] - offsets[None, :]), axis=2)
    group = distance[np.argmax(np.sum(distance <= 5, axis=1))] <= 5
    count = int(group.sum())
    selected = [tile for tile, keep in zip(tiles, group) if keep]
    points = np.array([[tile[0], tile[1]] for tile in selected])
    coverage = np.ptp(points, axis=0) / np.array([width, height])
    metadata.update(lighting_anchor_tiles=count, lighting_consensus=count/len(tiles),
                    lighting_coverage=coverage.tolist())
    if count < 6 or count/len(tiles) < .6 or min(coverage) < .35:
        return None, dict(metadata, lighting_reason='배경 변화가 여러 위치에서 일치하지 않음')
    center = np.median(offsets[group], axis=0)
    if np.max(np.abs(center)) > limit:
        return None, dict(metadata, lighting_reason='추가 조명 보정 한도 초과')
    # A low-order spatial light field is fitted only on confirmed background.
    # Local object changes cannot be fit away by per-pixel reference adaptation.
    coordinates = np.column_stack((np.ones(count), points[:, 0]/width-.5, points[:, 1]/height-.5))
    values = offsets[group]
    coefficients = np.linalg.lstsq(coordinates, values, rcond=None)[0]
    fitted = coordinates @ coefficients
    if np.max(np.median(np.abs(values-fitted), axis=0)) > 3:
        return None, dict(metadata, lighting_reason='배경 조명 추정 잔차 과다')
    # Do not extrapolate an unbounded gradient beyond the observed anchors.
    corners = np.array([[1, -.5, -.5], [1, .5, -.5], [1, -.5, .5], [1, .5, .5]]) @ coefficients
    if np.max(np.abs(corners)) > limit or np.max(np.ptp(corners, axis=0)) > 12:
        coefficients = np.vstack((center, np.zeros((2, 3))))
    yy, xx = np.mgrid[:height, :width]
    field = coefficients[0] + (xx[..., None]/width-.5)*coefficients[1] + (yy[..., None]/height-.5)*coefficients[2]
    return field.astype(np.float32), dict(metadata, lighting_reason='여러 배경 격자의 공통 조명 변화 확인')



def grid_energy(image):
    gray=cv2.cvtColor(image.astype(np.float32),cv2.COLOR_RGB2GRAY)
    gradient=cv2.magnitude(cv2.Sobel(gray,cv2.CV_32F,1,0,ksize=3),
                           cv2.Sobel(gray,cv2.CV_32F,0,1,ksize=3))
    return cv2.boxFilter(gradient,-1,(3,3))


def compare(current, reference, spec):
    reference, valid, registration = align(current, reference, spec['alignment_max_px'])
    a, b = current.astype(np.float32), reference.astype(np.float32)
    offset = np.median((a-b)[valid.astype(bool)], axis=0)
    clipped = float(np.mean(current.min(axis=2) >= 250))
    reference_clipped = float(np.mean(reference.min(axis=2) >= 250))
    clipped_limit = max(.45, reference_clipped+.1)
    metadata = dict(registration, comparison='fixed_pallet_precision', color_offset=offset.tolist(),
                    color_offset_limit=spec['max_color_offset'], adaptive_color_limit=spec['adaptive_color_limit'],
                    clipped_ratio=clipped, reference_clipped_ratio=reference_clipped,
                    clipped_limit=clipped_limit, quality_flags=[])
    if clipped > clipped_limit:
        return dict(metadata, quality_flags=['OVEREXPOSED'], candidate='UNKNOWN', reason='과노출 픽셀 비율 증가', features_computed=False)
    reference_energy=grid_energy(b)
    current_energy=grid_energy(a)
    texture_reference=float(np.percentile(reference_energy[valid.astype(bool)],90))
    texture_current=float(np.percentile(current_energy[valid.astype(bool)],90))
    texture_ratio=texture_current/max(1.,texture_reference)
    metadata['texture_ratio']=texture_ratio
    if texture_reference>40 and texture_ratio<.4:
        return dict(metadata,quality_flags=['TEXTURE_LOST'],candidate='UNKNOWN',
                    reason='영상 전체 격자 선명도 저하·가림 확인 필요',features_computed=False)
    radius = spec['reference_slack_px']
    kernel = np.ones((2*radius+1, 2*radius+1), np.uint8)
    hi = cv2.dilate(reference, kernel).astype(np.float32)
    lo = cv2.erode(reference, kernel).astype(np.float32)
    field, background = background_light(a, b, lo, hi, valid, spec['adaptive_color_limit'])
    metadata.update(background)
    if field is not None:
        corrected = a-field
        metadata.update(lighting_method='TEXTURED_BACKGROUND', applied_color_offset=np.median(field, axis=(0,1)).tolist())
    elif np.max(np.abs(offset)) <= spec['max_color_offset']:
        corrected = a-offset
        metadata.update(lighting_method='BOUNDED_MEDIAN', applied_color_offset=offset.tolist())
    else:
        return dict(metadata, quality_flags=['COLOR_OFFSET_LIMIT'], candidate='UNKNOWN',
                    reason='색상 변화가 큼·공통 배경 보정 근거 부족', features_computed=False)
    diff = np.max(np.maximum(lo-corrected, corrected-hi), axis=2)
    usable = valid.astype(bool)
    usable[[0,-1], :] = False
    usable[:, [0,-1]] = False
    width = spec['min_object_width_px']
    def mask(threshold):
        result = ((diff > threshold) & usable).astype(np.uint8)
        if width > 1:
            result = cv2.morphologyEx(result, cv2.MORPH_OPEN, np.ones((width,width), np.uint8))
        return result
    strong_mask, weak_mask = mask(spec['pixel_threshold']), mask(spec['weak_pixel_threshold'])
    def component(binary):
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        return int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
    strong, weak = component(strong_mask), component(weak_mask)
    # Pale products can have the same RGB as the lattice itself. A local,
    # two-dimensional disappearance of its texture is separate object evidence.
    old_energy,new_energy=reference_energy,grid_energy(corrected)
    loss=((old_energy>40)&(new_energy<old_energy*.4)&(new_energy<20)&usable).astype(np.uint8)
    loss=cv2.morphologyEx(loss,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    occlusion=component(loss)
    metadata['grid_occlusion_pixels']=occlusion
    ratio = float(weak_mask.sum()/max(1, usable.sum()))
    from .pallet import classify
    candidate=classify(strong,weak,occlusion,ratio,spec)
    reason={'PRESENT':'조명·작은 이동 보정 후 물체 크기 변화 확인',
            'EMPTY':'조명·작은 이동 보정 후 빈 격자와 일치',
            'UNKNOWN':'보정 후 남은 물체 변화가 불명확'}[candidate]
    return dict(metadata, candidate=candidate, reason=reason, component_pixels=strong,
                weak_component_pixels=weak, changed_ratio=ratio,
                raw_changed_ratio=float(np.mean((diff > spec['weak_pixel_threshold'])[usable])), features_computed=True)
