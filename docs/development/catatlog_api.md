# API List

Full layer detayları olan `featuretype & coverage` objeleri kabarık olduğu için bütün providerların bütün layerlarını tek apide çekmek doğru olmayacak. O yüzden önce providerları ve altlarındaki workspaceleri listeleyelim sonra da bu workspaceler ile layer detaylarını çekelim. Bunun için gerekenler:

## 1. Provider List

`provider/list`

Providerları listeler. Buradan provider id'lerini, diğer attributelarını ve özellikle workspace listesini çekeriz. Buradan alınan workspace bilgileri (muhtemelen id) ile de layer detaylarına gideriz. Tahmini payload şöyle olabilir:

```json
{
    "providers": [
        {
            "name": "Provider name",
            "id": 1,
            "url": "belki gerekir",
            "type": "geoserver ya da martin",
            "workspaces": [
                {
                    "name": "hamburg",
                    "id": 1,
                },
                {
                    "name": "poi",
                    "id": 2
                }
            ]
        }
    ]
}
```

## 2. Workspace Detail

`provider/{providerID}/workspaces/{workspaceID}`

Burada workspace altındaki layerların detaylarını çekeriz. Bu layer objeleri sende vardı not olarak. Yine tahmini şöyle bişey olabilir. 

```json
{
    "name": "hamburg",
    "id": 1,
    "layers": [
        {
            "type": "RASTER | VECTOR",
            "coverage": "eğer rastersa",
            "featureType": "eğer vectorse",
            "layerInfo": "ilk layer apisinden çektiğimiz şeyler burada"
        }
    ]
}
```

Bunlar da `coverage`, `featureType` ve `layerInfo` objelerinin typeları. Bu objelerin içinde geoserver urlleri var onları bi düşünüp çıkartmak gerekir.

```ts
/**
 * Buradan name, title, attributelist, keywords gibi bilgileri alıyoruz ama fazlasını da kullanabiliriz. Covarege için de aynısı geçerli sadece obje değiştiği için iki farklı type var.
 */
export interface GeoServerVectorTypeLayerDetail {
  featureType: {
    name: string;
    nativeName: string;
    namespace: {
      name: string;
      href: string;
    };
    title: string;
    abstract: string;
    keywords: {
      string: string[];
    };
    nativeCRS: string;
    srs: string;
    nativeBoundingBox: {
      minx: number;
      maxx: number;
      miny: number;
      maxy: number;
      crs: string;
    };
    latLonBoundingBox: {
      minx: number;
      maxx: number;
      miny: number;
      maxy: number;
      crs: string;
    };
    projectionPolicy: string;
    enabled: boolean;
    store: {
      "@class": string;
      name: string;
      href: string;
    };
    serviceConfiguration: boolean;
    simpleConversionEnabled: boolean;
    internationalTitle: string;
    internationalAbstract: string;
    maxFeatures: number;
    numDecimals: number;
    padWithZeros: boolean;
    forcedDecimal: boolean;
    overridingServiceSRS: boolean;
    skipNumberMatched: boolean;
    circularArcPresent: boolean;
    attributes: {
      attribute: GeoServerFeatureTypeAttribute[];
    };
  };
}
```

```ts
export interface GeoserverRasterTypeLayerDetail {
  coverage: {
    name: string,
    nativeName: string,
    namespace: {
      name: string,
      href: string
    },
    title: string,
    description: string,
    keywords: {
      string: string[]
    },
    nativeCRS: string
    srs: string,
    nativeBoundingBox: {
      minx: number,
      maxx: number,
      miny: number,
      maxy: number,
      crs: string
    },
    latLonBoundingBox: {
      minx: number,
      maxx: number,
      miny: number,
      maxy: number,
      crs: string
    },
    projectionPolicy: string,
    enabled: boolean,
    metadata: {
      entry: Array<Record<string, unknown>>
    },
    store: {
      "@class": string,
      name: string,
      href: string
    },
    serviceConfiguration: boolean,
    simpleConversionEnabled: boolean,
    internationalTitle: string,
    internationalAbstract: string,
    nativeFormat: string,
    grid: {
      "@dimension": number,
      range: {
        low: string,
        high: string
      },
      transform: {
        scaleX: string,
        scaleY: string,
        shearX: number,
        shearY: number,
        translateX: number,
        translateY: number
      },
      crs: string
    },
    supportedFormats: {
      string: string[]
    },
    interpolationMethods: {
      string: string[]
    },
    defaultInterpolationMethod: string,
    dimensions: {
      coverageDimension: Array<Record<string, unknown>>
    },
    requestSRS: {
      string: string
    },
    responseSRS: {
      string: string
    },
    parameters: {
      entry: Array<Record<string, unknown>>
    },
    nativeCoverageName: string
  }
}
```

```ts
/**
 * Buradan özellikle default stili ve varsa diğer stilleri alıyoruz. Ama created, updated gibi alanları da storeda layerları listelerken kullanabiliriz.
 */
export interface GeoserverLayerInfo {
  name: string;
  type: string;
  defaultStyle: {
    name: string;
    href: string;
  };
  resource: {
    "@class": string;
    name: string;
    href: string;
  };
  attribution: {
    logoWidth: number;
    logoHeight: number;
  };
  dateCreated: string;
  dateModified: string
  styles: {
  "@class": string;
  style: [
    {
        name: string;
        href: string;
    },
    {
        name: string;
        href: string;
    }
  ]
}
}
```

## 3. Style List

`styles/list`

Bütün stilleri listeler. Şu an için mbstyle'lar ana hedef ama sonrasında sld işine de bakmak lazım raster layerların stillerini değiştirmek için. layer requestini o stille atmalık

```json
{
    "styles":[
        {
            "name": "Parcel",
            "id": 1,
            "type": "mbstyle",
        }
    ]
}
```

## 4. Style Detail

`styles/{styleID}`

İstenen stili verir.

```json
{
    "mbstyleobjesi":"direkt olarak mbstyle objesini response dönelim."
}
```
